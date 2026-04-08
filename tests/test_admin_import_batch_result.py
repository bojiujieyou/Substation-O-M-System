import io
import json
import sqlite3

import admin as admin_module
from app import app
from auth import hash_password
from init_db import init_db


def _login(client):
    response = client.post("/auth/login", json={"username": "admin1", "password": "adminpass"})
    assert response.status_code == 200


def _prepare_review_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        ALTER TABLE fault_reports ADD COLUMN project_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN source_type TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_batch_id TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_record_key TEXT;
        ALTER TABLE fault_reports ADD COLUMN fault_type_label_snapshot TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_time_raw TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_timezone TEXT;

        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#1a73e8',
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            file_count INTEGER,
            success_count INTEGER,
            fail_count INTEGER,
            report_path TEXT,
            operator_id INTEGER,
            timezone_default_used TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE station_name_mapping_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_batch_id INTEGER,
            project_id INTEGER,
            source_system TEXT NOT NULL,
            external_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            candidate_station_id INTEGER,
            confidence_score REAL,
            raw_context_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_id INTEGER,
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE fault_import_review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_batch_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_record_key_candidate TEXT,
            raw_payload_json TEXT NOT NULL,
            issue_type TEXT NOT NULL,
            issue_detail TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_fault_id INTEGER,
            reviewer_id INTEGER,
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, sort_order, is_active)
        VALUES (1, 'inspection', 'Inspection', 'INSP', 1, 1)
        """
    )
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Station A', '110kV', 'County A')"
    )

    password_hash = hash_password("adminpass")
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'admin1', ?, 'admin')",
        (password_hash,),
    )
    conn.commit()
    return conn


def test_import_batch_result_page_renders_for_admin(test_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        conn.execute(
            """
            INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count, created_at)
            VALUES (7, 1, 'import_faults', 'best-effort', 1, 1, 0, '2026-04-06T12:00:00Z')
            """
        )
        conn.commit()
        conn.close()

        with app.test_client() as client:
            _login(client)
            response = client.get("/admin/import-batches/7")
            assert response.status_code == 200
            assert "导入结果摘要".encode("utf-8") in response.data
            assert b"admin-section-title" in response.data
            assert b"source-summary-content" in response.data
            assert b"next-step-primary-emphasis" in response.data
            assert b"setInlineMessage(" in response.data
            assert b"is-hidden" in response.data
            assert b"next-steps-secondary" in response.data
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path


def test_import_batch_summary_returns_pending_conflicts_and_project_scoped_cta(test_db, tmp_path):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        report_path = tmp_path / "batch7-report.json"
        report_path.write_text(json.dumps({"summary": {"inserted": 3, "queue_items_created": 1, "station_proposals_created": 1}, "files": [{"name": "faults.xlsx", "status": "pending", "message": "待审查"}], "rows": [{"source_record_key": "rk-2", "issue_type": "station_not_resolved", "issue_detail": "needs station"}]}, ensure_ascii=False), encoding="utf-8")

        conn.execute(
            """
            INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count, report_path, created_at)
            VALUES (7, 1, 'import_faults', 'best-effort', 1, 3, 1, ?, '2026-04-06T12:00:00Z')
            """,
            (str(report_path),),
        )
        conn.execute(
            """
            INSERT INTO fault_reports (
                id, station_id, project_id, source_type, source_batch_id, source_record_key,
                fault_type, fault_type_label_snapshot, description, status, created_at, updated_at
            ) VALUES (
                1, 1, 1, 'import_faults', '7', 'rk-1', '网络故障', '网络故障', 'row 1', 'open', '2026-04-06T12:01:00Z', '2026-04-06T12:01:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fault_import_review_queue (
                id, import_batch_id, project_id, source_type, source_record_key_candidate,
                raw_payload_json, issue_type, issue_detail, status
            ) VALUES (
                1, 7, 1, 'import_faults', 'rk-2', '{}', 'station_not_resolved', 'needs station', 'pending'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO station_name_mapping_proposals (
                id, import_batch_id, project_id, source_system, external_name,
                normalized_name, candidate_station_id, confidence_score, status
            ) VALUES (
                1, 7, 1, 'import_faults', 'External A', 'externala', 1, 0.8, 'pending'
            )
            """
        )
        conn.commit()
        conn.close()

        with app.test_client() as client:
            _login(client)
            response = client.get("/admin/import-batches/7/summary")
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["page_status"] == "pending_conflicts"
            assert payload["pending_review_count"] == 1
            assert payload["pending_proposal_count"] == 1
            assert payload["primary_cta"] == "前往导入审查中心"
            assert payload["primary_cta_url"] == "/admin/review-center?project=inspection"
            assert payload["summary"]["review_issue_type_breakdown"] == {"station_not_resolved": 1}
            assert payload["source_summary"]["inserted"] == 3
            assert payload["source_summary"]["queued"] == 1
            assert payload["source_summary"]["proposals"] == 1
            assert payload["source_summary"]["file_result_preview"] == [{"text": "faults.xlsx / pending / 待审查", "tone": "warning"}]
            assert payload["source_summary"]["row_result_preview"] == [{"text": "rk-2 / station_not_resolved / needs station", "tone": "info"}]
            assert payload["next_steps"][0] == "先处理 1 条待确认记录，避免异常数据继续留在队列中。"
            assert payload["next_steps"][1] == "再处理 1 条站名提议，确认站点映射是否正确。"
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path


def test_admin_ai_status_endpoint_returns_runtime_state(test_db, monkeypatch):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        conn.close()

        monkeypatch.setattr(
            admin_module,
            "probe_nvidia_health",
            lambda: {
                "provider": "nvidia",
                "status": "error",
                "message": "NVIDIA AI unavailable, fallback enabled",
                "last_error": "timeout",
                "configured": True,
                "enabled": False,
                "model": "demo-model",
                "last_operation": "health_probe",
                "last_method": "POST",
                "last_endpoint": "/chat/completions",
            },
        )

        with app.test_client() as client:
            _login(client)
            response = client.get("/admin/ai-status")
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["provider"] == "nvidia"
            assert payload["status"] == "error"
            assert payload["message"] == "NVIDIA AI unavailable, fallback enabled"
            assert payload["last_error"] == "timeout"
            assert payload["model"] == "demo-model"
            assert payload["last_operation"] == "health_probe"
            assert payload["last_method"] == "POST"
            assert payload["last_endpoint"] == "/chat/completions"
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path


def test_import_batch_summary_returns_report_derived_excel_metrics_without_fault_rows(test_db, tmp_path):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        report_path = tmp_path / "batch9-report.json"
        report_path.write_text(
            json.dumps(
                {
                    "summary": {"stations_processed": 2, "cameras_processed": 8},
                    "files": [{"name": "inventory.xlsx"}],
                    "rows": [{"row": 1}, {"row": 2}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        conn.execute(
            """
            INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count, report_path, created_at)
            VALUES (9, 1, 'import_excel', 'best-effort', 1, 2, 0, ?, '2026-04-06T13:00:00Z')
            """,
            (str(report_path),),
        )
        conn.commit()
        conn.close()

        with app.test_client() as client:
            _login(client)
            response = client.get("/admin/import-batches/9/summary")
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["page_status"] == "success"
            assert payload["summary"]["fault_rows"] == 0
            assert payload["source_summary"]["station_count"] == 2
            assert payload["source_summary"]["camera_count"] == 8
            assert len(payload["source_summary"]["file_results"]) == 1
            assert len(payload["source_summary"]["row_results"]) == 2
            assert payload["source_summary"]["file_result_preview"] == [{"text": "inventory.xlsx", "tone": "info"}]
            assert payload["source_summary"]["row_result_preview"] == [{"text": "第1行 / 未命名站点", "tone": "info"}, {"text": "第2行 / 未命名站点", "tone": "info"}]
            assert payload["next_steps"][0] == "可以抽查站点和摄像头数据，确认本次台账导入覆盖到了预期范围。"
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path


def test_import_batch_summary_returns_404_for_missing_batch(test_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        conn.close()

        with app.test_client() as client:
            _login(client)
            response = client.get("/admin/import-batches/999/summary")
            assert response.status_code == 404
            assert response.get_json()["error"] == "导入批次不存在"
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path


def test_import_batch_summary_marks_inventory_with_zero_cameras_as_partial_success(test_db, tmp_path):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        report_path = tmp_path / "batch10-report.json"
        report_path.write_text(
            json.dumps(
                {
                    "project": "inspection",
                    "file_count": 1,
                    "station_count": 1,
                    "camera_count": 0,
                    "success_count": 1,
                    "fail_count": 0,
                    "rows": [
                        {
                            "file": "daily.xls",
                            "station": "变电站视频系统监控日报",
                            "status": "imported",
                            "camera_rows": 0,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        conn.execute(
            """
            INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count, report_path, created_at)
            VALUES (10, 1, 'import_excel', 'best-effort', 1, 1, 0, ?, '2026-04-07T14:16:20Z')
            """,
            (str(report_path),),
        )
        conn.commit()
        conn.close()

        with app.test_client() as client:
            _login(client)
            response = client.get("/admin/import-batches/10/summary")
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["page_status"] == "partial_success"
            assert payload["source_summary"]["station_count"] == 1
            assert payload["source_summary"]["camera_count"] == 0
            assert payload["next_steps"][0] == "这次是台账导入，只会更新站点和摄像头，不会出现在故障列表中。"
            assert payload["next_steps"][1] == "当前没有写入任何摄像头，请检查上传文件是否真的是摄像头台账，而不是监控日报或其他报表。"
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path


def test_import_batch_summary_treats_daily_fault_summary_as_fault_import(test_db, tmp_path):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        report_path = tmp_path / "batch11-report.json"
        report_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "inserted": 2,
                        "queue_items_created": 1,
                        "station_proposals_created": 1,
                        "duplicates_skipped": 1,
                    },
                    "files": [{"name": "daily-summary.xls", "status": "partial_success", "message": "写入 2 条，待审查 1 条"}],
                    "rows": [{"id": "第6行 Station A", "status": "inserted", "message": "摄像头离线 / 主变西北侧球机离线"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        conn.execute(
            """
            INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count, report_path, created_at)
            VALUES (11, 1, 'import_daily_fault_summary', 'best-effort', 1, 2, 1, ?, '2026-04-07T15:00:00Z')
            """,
            (str(report_path),),
        )
        conn.commit()
        conn.close()

        with app.test_client() as client:
            _login(client)
            response = client.get("/admin/import-batches/11/summary")
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["page_status"] == "partial_success"
            assert payload["source_summary"]["inserted"] == 2
            assert payload["source_summary"]["queued"] == 1
            assert payload["source_summary"]["proposals"] == 1
            assert payload["source_summary"]["duplicates_skipped"] == 1
            assert payload["next_steps"][0] == "先核对失败项和导入报告，确认哪些文件或记录需要补录。"
            assert payload["next_steps"][1] == "重点复核未入库的故障行，避免漏掉真实故障。"
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path


def test_admin_upload_allows_blank_county_and_preserves_existing_county(test_db, monkeypatch):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = test_db
    app.config["DATABASE_PATH"] = test_db
    app.config["TESTING"] = True

    try:
        init_db(force=True)
        conn = _prepare_review_db(test_db)
        conn.executescript(
            """
            CREATE TABLE camera_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_code TEXT NOT NULL,
                station_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                location_desc TEXT NOT NULL DEFAULT '',
                area TEXT NOT NULL DEFAULT '',
                channel_number INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(station_id, project_id, slot_code)
            );

            DROP TABLE cameras;
            CREATE TABLE cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id INTEGER,
                station_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                project_camera_code TEXT,
                camera_index TEXT,
                area TEXT,
                location_desc TEXT,
                ip_address TEXT,
                channel_port INTEGER,
                channel_number INTEGER,
                status TEXT DEFAULT 'active',
                replaced_by_camera_id INTEGER,
                retired_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX idx_cameras_one_active_per_slot
            ON cameras(slot_id)
            WHERE status = 'active';
            """
        )
        conn.execute(
            "UPDATE stations SET county = 'County A' WHERE id = 1"
        )
        conn.commit()
        conn.close()

        parse_results = iter([
            {
                "station": {
                    "name": "Station A",
                    "voltage_level": "110kV",
                    "county": "",
                    "location": "Location A",
                    "ip_range": "10.0.0.0/24",
                    "nvr_ip": "10.0.0.10",
                    "nvr_port": 8000,
                },
                "cameras": [
                    {
                        "camera_index": "1",
                        "area": "Area A",
                        "location": "North Gate",
                        "ip_address": "10.0.0.11",
                        "channel_port": 1,
                        "channel_number": 1,
                        "slot_code": "SLOT-A-1",
                        "project_camera_code": "CAM-A-1",
                    }
                ],
            },
            {
                "station": {
                    "name": "Station B",
                    "voltage_level": "35kV",
                    "county": "",
                    "location": "Location B",
                    "ip_range": "10.0.1.0/24",
                    "nvr_ip": "10.0.1.10",
                    "nvr_port": 8001,
                },
                "cameras": [
                    {
                        "camera_index": "1",
                        "area": "Area B",
                        "location": "South Gate",
                        "ip_address": "10.0.1.11",
                        "channel_port": 1,
                        "channel_number": 1,
                        "slot_code": "SLOT-B-1",
                        "project_camera_code": "CAM-B-1",
                    }
                ],
            },
        ])

        monkeypatch.setattr(admin_module, "parse_excel_admin", lambda _filepath: next(parse_results))

        with app.test_client() as client:
            _login(client)

            response_existing = client.post(
                "/admin/upload",
                data={
                    "project": "inspection",
                    "county": "",
                    "file": (io.BytesIO(b"existing"), "existing.xlsx"),
                },
                content_type="multipart/form-data",
            )
            assert response_existing.status_code == 200
            payload_existing = response_existing.get_json()
            assert payload_existing["message"] == "导入成功"
            assert payload_existing["project"] == "inspection"

            response_new = client.post(
                "/admin/upload",
                data={
                    "project": "inspection",
                    "county": "",
                    "file": (io.BytesIO(b"new"), "new.xlsx"),
                },
                content_type="multipart/form-data",
            )
            assert response_new.status_code == 200
            payload_new = response_new.get_json()
            assert payload_new["message"] == "导入成功"
            assert payload_new["project"] == "inspection"

        conn = sqlite3.connect(test_db)
        try:
            existing_station = conn.execute(
                "SELECT county FROM stations WHERE name = 'Station A' AND voltage_level = '110kV'"
            ).fetchone()
            new_station = conn.execute(
                "SELECT county FROM stations WHERE name = 'Station B' AND voltage_level = '35kV'"
            ).fetchone()
            batch_projects = conn.execute(
                "SELECT project_id, success_count, fail_count FROM import_batches ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

        assert existing_station == ("County A",)
        assert new_station == ("",)
        assert batch_projects == [(1, 1, 0), (1, 1, 0)]
    finally:
        config_module.Config.DATABASE_PATH = original_path
        app.config["DATABASE_PATH"] = app_original_db_path
