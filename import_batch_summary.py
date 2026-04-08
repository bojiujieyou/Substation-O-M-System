from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from import_review_support import get_columns, table_exists
from utils import create_db_connection


STATUS_SUCCESS = "success"
STATUS_PARTIAL_SUCCESS = "partial_success"
STATUS_PENDING_CONFLICTS = "pending_conflicts"
STATUS_FAILED = "failed"
FAULT_LIKE_SOURCE_TYPES = {"import_faults", "import_daily_fault_summary", "import_worklog"}


def _row_to_dict(row):
    if row is None:
        return None
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _read_report_json(report_path: str | Path | None) -> dict | None:
    if not report_path:
        return None
    path = Path(report_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def fetch_batch_metadata(conn, batch_id: int) -> dict | None:
    if not table_exists(conn, "import_batches"):
        return None
    row = conn.execute(
        """
        SELECT
            b.id,
            b.project_id,
            p.code AS project_code,
            p.name AS project_name,
            b.source_type,
            b.mode,
            b.file_count,
            b.success_count,
            b.fail_count,
            b.report_path,
            b.operator_id,
            b.timezone_default_used,
            b.created_at
        FROM import_batches b
        LEFT JOIN projects p ON p.id = b.project_id
        WHERE b.id = ?
        """,
        (batch_id,),
    ).fetchone()
    return _row_to_dict(row)


def fetch_fault_rows(conn, batch_id: int) -> list[dict]:
    if not table_exists(conn, "fault_reports"):
        return []
    columns = get_columns(conn, "fault_reports")
    if "source_batch_id" not in columns:
        return []

    select_parts = ["id", "station_id", "camera_id", "status", "created_at", "updated_at"]
    for optional in [
        "project_id",
        "camera_slot_id",
        "source_type",
        "source_record_key",
        "fault_type",
        "fault_type_code",
        "fault_type_label_snapshot",
        "description",
        "project_device_code",
    ]:
        if optional in columns:
            select_parts.append(optional)

    rows = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
        FROM fault_reports
        WHERE source_batch_id = ?
        ORDER BY id
        """,
        (str(batch_id),),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def fetch_review_rows(conn, batch_id: int) -> list[dict]:
    if not table_exists(conn, "fault_import_review_queue"):
        return []
    rows = conn.execute(
        """
        SELECT
            id,
            project_id,
            source_type,
            source_record_key_candidate,
            issue_type,
            issue_detail,
            status,
            resolved_fault_id,
            reviewer_id,
            reviewed_at,
            created_at
        FROM fault_import_review_queue
        WHERE import_batch_id = ?
        ORDER BY id
        """,
        (batch_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def fetch_proposal_rows(conn, batch_id: int) -> list[dict]:
    if not table_exists(conn, "station_name_mapping_proposals"):
        return []
    rows = conn.execute(
        """
        SELECT
            id,
            project_id,
            source_system,
            external_name,
            normalized_name,
            candidate_station_id,
            confidence_score,
            status,
            reviewer_id,
            reviewed_at,
            created_at
        FROM station_name_mapping_proposals
        WHERE import_batch_id = ?
        ORDER BY id
        """,
        (batch_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def build_breakdown(rows: list[dict], key: str) -> dict:
    counter = Counter()
    for row in rows:
        counter[str(row.get(key) or "NULL")] += 1
    return dict(sorted(counter.items()))


def build_import_batch_report(*, metadata: dict, fault_rows: list[dict], review_rows: list[dict], proposal_rows: list[dict]) -> dict:
    return {
        "batch": metadata,
        "summary": {
            "fault_rows": len(fault_rows),
            "review_queue_rows": len(review_rows),
            "station_name_proposals": len(proposal_rows),
            "fault_status_breakdown": build_breakdown(fault_rows, "status"),
            "review_status_breakdown": build_breakdown(review_rows, "status"),
            "review_issue_type_breakdown": build_breakdown(review_rows, "issue_type"),
            "proposal_status_breakdown": build_breakdown(proposal_rows, "status"),
        },
        "fault_rows": fault_rows,
        "review_rows": review_rows,
        "proposal_rows": proposal_rows,
    }


def _build_preview_items(items: list[dict] | None, source_type: str, kind: str) -> list[dict]:
    if not isinstance(items, list):
        return []

    preview = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        if kind == "file":
            label = item.get("name") or item.get("file") or item.get("filename") or "未命名文件"
            status = item.get("status") or item.get("result") or ""
            detail = item.get("message") or item.get("error") or item.get("summary") or ""
            parts = [str(label)]
            if status:
                parts.append(str(status))
            if detail:
                parts.append(str(detail))
            preview.append(
                {
                    "text": " / ".join(parts),
                    "tone": _resolve_preview_tone(status, detail),
                }
            )
            continue

        if source_type == "import_excel":
            row_no = item.get("row") or item.get("row_number") or item.get("index")
            station = item.get("station") or item.get("station_name") or "未命名站点"
            status = item.get("status") or item.get("result") or ""
            detail = item.get("error") or item.get("message") or ""
            prefix = f"第{row_no}行" if row_no not in (None, "") else "行记录"
            parts = [prefix, str(station)]
            if status:
                parts.append(str(status))
            if detail:
                parts.append(str(detail))
            preview.append(
                {
                    "text": " / ".join(parts),
                    "tone": _resolve_preview_tone(status, detail),
                }
            )
            continue

        key = item.get("source_record_key") or item.get("source_record_key_candidate") or item.get("id") or "记录"
        issue = item.get("issue_type") or item.get("status") or item.get("result") or ""
        detail = item.get("issue_detail") or item.get("message") or item.get("error") or ""
        parts = [str(key)]
        if issue:
            parts.append(str(issue))
        if detail:
            parts.append(str(detail))
        preview.append(
            {
                "text": " / ".join(parts),
                "tone": _resolve_preview_tone(issue, detail),
            }
        )

    return preview


def _resolve_preview_tone(status: object, detail: object) -> str:
    text = f"{status or ''} {detail or ''}".lower()
    if any(keyword in text for keyword in ["success", "ok", "done", "成功", "已完成", "approved"]):
        return "success"
    if any(keyword in text for keyword in ["pending", "queue", "review", "待处理", "待确认", "排队", "proposal"]):
        return "warning"
    if any(keyword in text for keyword in ["fail", "failed", "error", "reject", "失败", "错误", "驳回", "冲突"]):
        return "danger"
    return "info"


def _extract_report_source_summary(source_type: str, report_data: dict | None) -> dict:
    if not report_data:
        return {}

    summary = report_data.get("summary") if isinstance(report_data.get("summary"), dict) else {}
    source_summary = {
        "report_generated": True,
        "raw_summary": summary,
    }

    if source_type == "import_excel":
        file_results = report_data.get("files") or report_data.get("file_results") or []
        row_results = report_data.get("rows") or report_data.get("row_results") or []
        station_count = (
            summary.get("stations_processed")
            or summary.get("station_count")
            or report_data.get("station_count")
            or 0
        )
        camera_count = (
            summary.get("cameras_processed")
            or summary.get("camera_count")
            or report_data.get("camera_count")
            or 0
        )
        source_summary.update(
            {
                "station_count": station_count,
                "camera_count": camera_count,
                "file_results": file_results,
                "row_results": row_results,
                "file_result_preview": _build_preview_items(file_results, source_type, "file"),
                "row_result_preview": _build_preview_items(row_results, source_type, "row"),
            }
        )
        return source_summary

    if source_type in FAULT_LIKE_SOURCE_TYPES:
        file_results = report_data.get("files") or report_data.get("file_results") or []
        row_results = report_data.get("rows") or report_data.get("row_results") or []
        source_summary.update(
            {
                "inserted": summary.get("inserted") or 0,
                "queued": summary.get("queue_items_created") or summary.get("queued") or 0,
                "proposals": summary.get("station_proposals_created") or summary.get("proposals") or 0,
                "duplicates_skipped": summary.get("duplicates_skipped") or 0,
                "rows_skipped": summary.get("rows_skipped") or 0,
                "file_results": file_results,
                "row_results": row_results,
                "file_result_preview": _build_preview_items(file_results, source_type, "file"),
                "row_result_preview": _build_preview_items(row_results, source_type, "row"),
            }
        )
        return source_summary

    return source_summary


def _is_empty_inventory_success(metadata: dict, report: dict, source_summary: dict) -> bool:
    if (metadata.get("source_type") or "") != "import_excel":
        return False
    if report["summary"]["review_queue_rows"] > 0 or report["summary"]["station_name_proposals"] > 0:
        return False
    station_count = int(source_summary.get("station_count") or 0)
    camera_count = int(source_summary.get("camera_count") or 0)
    return station_count > 0 and camera_count <= 0


def _derive_page_status(metadata: dict, report: dict, source_summary: dict) -> str:
    summary = report["summary"]
    has_pending = summary["review_queue_rows"] > 0 or summary["station_name_proposals"] > 0
    success_count = int(metadata.get("success_count") or 0)
    fail_count = int(metadata.get("fail_count") or 0)

    if has_pending:
        return STATUS_PENDING_CONFLICTS
    if _is_empty_inventory_success(metadata, report, source_summary):
        return STATUS_PARTIAL_SUCCESS
    if fail_count > 0 and success_count > 0:
        return STATUS_PARTIAL_SUCCESS
    if fail_count > 0 and success_count <= 0:
        return STATUS_FAILED
    return STATUS_SUCCESS


def _build_primary_cta(metadata: dict, page_status: str, report: dict) -> tuple[str, str]:
    project_code = metadata.get("project_code") or ""
    review_count = report["summary"]["review_queue_rows"]
    proposal_count = report["summary"]["station_name_proposals"]

    if page_status == STATUS_PENDING_CONFLICTS or review_count or proposal_count:
        return "前往导入审查中心", f"/admin/review-center?project={project_code}" if project_code else "/admin/review-center"
    return "返回管理后台", f"/admin?project={project_code}" if project_code else "/admin"


def _build_next_steps(metadata: dict, page_status: str, report: dict, source_summary: dict) -> list[str]:
    source_type = metadata.get("source_type") or ""
    review_count = report["summary"]["review_queue_rows"]
    proposal_count = report["summary"]["station_name_proposals"]
    fail_count = int(metadata.get("fail_count") or 0)

    if page_status == STATUS_PENDING_CONFLICTS:
        steps = []
        if review_count:
            steps.append(f"先处理 {review_count} 条待确认记录，避免异常数据继续留在队列中。")
        if proposal_count:
            steps.append(f"再处理 {proposal_count} 条站名提议，确认站点映射是否正确。")
        steps.append("全部清完后再回看批次结果，确认这次导入是否真正收口。")
        return steps

    if page_status == STATUS_PARTIAL_SUCCESS:
        if _is_empty_inventory_success(metadata, report, source_summary):
            return [
                "这次是台账导入，只会更新站点和摄像头，不会出现在故障列表中。",
                "当前没有写入任何摄像头，请检查上传文件是否真的是摄像头台账，而不是监控日报或其他报表。",
                "修正文件后重新导入，再到站点和摄像头数据里复核结果。",
            ]
        steps = ["先核对失败项和导入报告，确认哪些文件或记录需要补录。"]
        if source_type in FAULT_LIKE_SOURCE_TYPES:
            steps.append("重点复核未入库的故障行，避免漏掉真实故障。")
        else:
            steps.append("重点复核未成功导入的台账内容，避免站点或摄像头信息不完整。")
        return steps

    if page_status == STATUS_FAILED:
        return [
            "先检查导入文件和报告内容，确认失败发生在解析、映射还是入库阶段。",
            "修正输入数据后重新导入，不要直接假设系统里已经有可用结果。",
        ]

    if source_type in FAULT_LIKE_SOURCE_TYPES:
        return [
            "可以抽查已写入的故障记录，确认关键字段和项目归属无误。",
            "如果这是批量补录，建议继续回到管理后台处理下一批。",
        ]

    return [
        "可以抽查站点和摄像头数据，确认本次台账导入覆盖到了预期范围。",
        "确认无误后返回管理后台，继续后续导入或日常维护工作。",
    ]


def build_import_batch_summary(*, database: str | Path, batch_id: int) -> dict:
    conn = create_db_connection(database, row_factory=True)
    try:
        metadata = fetch_batch_metadata(conn, batch_id)
        if metadata is None:
            raise RuntimeError("import_batches table not found")
        if not metadata:
            raise RuntimeError(f"import batch not found: {batch_id}")

        fault_rows = fetch_fault_rows(conn, batch_id)
        review_rows = fetch_review_rows(conn, batch_id)
        proposal_rows = fetch_proposal_rows(conn, batch_id)
        report = build_import_batch_report(
            metadata=metadata,
            fault_rows=fault_rows,
            review_rows=review_rows,
            proposal_rows=proposal_rows,
        )
        report_json = _read_report_json(metadata.get("report_path"))
        source_summary = _extract_report_source_summary(metadata.get("source_type") or "", report_json)
        page_status = _derive_page_status(metadata, report, source_summary)
        primary_cta, primary_cta_url = _build_primary_cta(metadata, page_status, report)

        return {
            **report,
            "page_status": page_status,
            "has_pending_items": page_status == STATUS_PENDING_CONFLICTS,
            "pending_review_count": report["summary"]["review_queue_rows"],
            "pending_proposal_count": report["summary"]["station_name_proposals"],
            "primary_cta": primary_cta,
            "primary_cta_url": primary_cta_url,
            "project_code": metadata.get("project_code") or "",
            "report_available": bool(report_json),
            "source_summary": source_summary,
            "next_steps": _build_next_steps(metadata, page_status, report, source_summary),
        }
    finally:
        conn.close()
