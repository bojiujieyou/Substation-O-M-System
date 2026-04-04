import hashlib
import json
import re


PROJECT_CODE_BY_SYSTEM_TYPE = {
    "图像监控": "unified",
    "智能巡视": "inspection",
    "辅控系统": "auxiliary",
}


def table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def get_columns(conn, table_name):
    if not table_exists(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def normalize_station_name(value):
    if not value:
        return ""
    text = str(value).strip().lower()
    for token in [
        " ",
        "\t",
        "_",
        "-",
        "/",
        "\\",
        "（",
        "）",
        "(",
        ")",
        "【",
        "】",
        "[",
        "]",
        "变电站",
        "变",
        "站",
    ]:
        text = text.replace(token, "")
    return text


def project_code_from_system_type(system_type):
    return PROJECT_CODE_BY_SYSTEM_TYPE.get((system_type or "").strip())


def get_project_row(conn, project_code):
    if not project_code or not table_exists(conn, "projects"):
        return None
    row = conn.execute(
        "SELECT id, code, name FROM projects WHERE code = ?",
        (project_code,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0] if not hasattr(row, "keys") else row["id"],
        "code": row[1] if not hasattr(row, "keys") else row["code"],
        "name": row[2] if not hasattr(row, "keys") else row["name"],
    }


def multi_project_import_enabled(conn):
    required_tables = {
        "projects",
        "import_batches",
        "station_name_mapping_proposals",
        "fault_import_review_queue",
    }
    if not all(table_exists(conn, name) for name in required_tables):
        return False
    fault_columns = get_columns(conn, "fault_reports")
    return {
        "project_id",
        "source_type",
        "source_batch_id",
        "source_record_key",
        "fault_type_label_snapshot",
    }.issubset(fault_columns)


def create_import_batch(
    conn,
    *,
    project_id,
    source_type,
    mode,
    file_count=1,
    report_path=None,
    operator_id=None,
    timezone_default_used="Asia/Shanghai",
):
    cursor = conn.execute(
        """
        INSERT INTO import_batches (
            project_id, source_type, mode, file_count,
            success_count, fail_count, report_path, operator_id, timezone_default_used
        )
        VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)
        """,
        (
            project_id,
            source_type,
            mode,
            file_count,
            report_path,
            operator_id,
            timezone_default_used,
        ),
    )
    return cursor.lastrowid


def update_import_batch_stats(conn, batch_id, *, success_count, fail_count):
    conn.execute(
        """
        UPDATE import_batches
        SET success_count = ?, fail_count = ?
        WHERE id = ?
        """,
        (success_count, fail_count, batch_id),
    )


def build_source_record_key(project_code, source_type, *, raw_external_id=None, canonical_row=None):
    seed = None
    if raw_external_id not in (None, ""):
        seed = str(raw_external_id)
    elif canonical_row not in (None, ""):
        seed = str(canonical_row)
    if not seed:
        return None
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{project_code}:{source_type}:{digest}"


def fault_source_key_exists(conn, source_record_key):
    if not source_record_key:
        return False
    columns = get_columns(conn, "fault_reports")
    if "source_record_key" not in columns:
        return False
    row = conn.execute(
        "SELECT 1 FROM fault_reports WHERE source_record_key = ?",
        (source_record_key,),
    ).fetchone()
    return row is not None


def _load_station_reference_data(conn, source_system):
    stations = conn.execute("SELECT id, name, county FROM stations").fetchall()
    station_rows = []
    for row in stations:
        station_id = row[0] if not hasattr(row, "keys") else row["id"]
        name = row[1] if not hasattr(row, "keys") else row["name"]
        county = row[2] if not hasattr(row, "keys") else row["county"]
        station_rows.append(
            {
                "id": station_id,
                "name": name,
                "county": county,
                "normalized_name": normalize_station_name(name),
            }
        )

    alias_map = {}
    if table_exists(conn, "station_aliases"):
        for row in conn.execute("SELECT station_id, alias FROM station_aliases").fetchall():
            station_id = row[0] if not hasattr(row, "keys") else row["station_id"]
            alias = row[1] if not hasattr(row, "keys") else row["alias"]
            alias_map.setdefault(normalize_station_name(alias), set()).add(station_id)

    external_map = {}
    if source_system and table_exists(conn, "station_external_names"):
        rows = conn.execute(
            """
            SELECT station_id, external_name, normalized_name
            FROM station_external_names
            WHERE source_system = ?
            """,
            (source_system,),
        ).fetchall()
        for row in rows:
            station_id = row[0] if not hasattr(row, "keys") else row["station_id"]
            external_name = row[1] if not hasattr(row, "keys") else row["external_name"]
            normalized_name = row[2] if not hasattr(row, "keys") else row["normalized_name"]
            external_map.setdefault(normalize_station_name(external_name), set()).add(station_id)
            if normalized_name:
                external_map.setdefault(normalized_name, set()).add(station_id)

    return station_rows, alias_map, external_map


def resolve_station_match(conn, external_name, *, source_system=None):
    normalized = normalize_station_name(external_name)
    if not normalized:
        return {
            "matched": False,
            "station_id": None,
            "station_name": None,
            "confidence_score": None,
            "match_source": "empty",
            "should_create_proposal": False,
            "proposal_candidate_station_id": None,
        }

    station_rows, alias_map, external_map = _load_station_reference_data(conn, source_system)

    if normalized in external_map:
        station_ids = sorted(external_map[normalized])
        if len(station_ids) == 1:
            station_id = station_ids[0]
            station_name = next(
                (row["name"] for row in station_rows if row["id"] == station_id),
                None,
            )
            return {
                "matched": True,
                "station_id": station_id,
                "station_name": station_name,
                "confidence_score": 0.9,
                "match_source": "station_external_names",
                "should_create_proposal": False,
                "proposal_candidate_station_id": station_id,
            }

    if normalized in alias_map:
        station_ids = sorted(alias_map[normalized])
        if len(station_ids) == 1:
            station_id = station_ids[0]
            station_name = next(
                (row["name"] for row in station_rows if row["id"] == station_id),
                None,
            )
            return {
                "matched": True,
                "station_id": station_id,
                "station_name": station_name,
                "confidence_score": 0.9,
                "match_source": "station_aliases",
                "should_create_proposal": True,
                "proposal_candidate_station_id": station_id,
            }

    exact_matches = [row for row in station_rows if row["normalized_name"] == normalized]
    if len(exact_matches) == 1:
        match = exact_matches[0]
        return {
            "matched": True,
            "station_id": match["id"],
            "station_name": match["name"],
            "confidence_score": 1.0,
            "match_source": "stations_exact",
            "should_create_proposal": False,
            "proposal_candidate_station_id": match["id"],
        }

    fuzzy_matches = [
        row
        for row in station_rows
        if normalized in row["normalized_name"] or row["normalized_name"] in normalized
    ]
    unique_fuzzy = {row["id"]: row for row in fuzzy_matches}
    if len(unique_fuzzy) == 1:
        match = next(iter(unique_fuzzy.values()))
        return {
            "matched": True,
            "station_id": match["id"],
            "station_name": match["name"],
            "confidence_score": 0.7,
            "match_source": "stations_fuzzy",
            "should_create_proposal": True,
            "proposal_candidate_station_id": match["id"],
        }

    return {
        "matched": False,
        "station_id": None,
        "station_name": None,
        "confidence_score": None,
        "match_source": "unresolved",
        "should_create_proposal": True,
        "proposal_candidate_station_id": None,
    }


def ensure_station_name_proposal(
    conn,
    *,
    import_batch_id,
    project_id,
    source_system,
    external_name,
    candidate_station_id,
    confidence_score,
    raw_context,
):
    if not table_exists(conn, "station_name_mapping_proposals"):
        return None

    normalized_name = normalize_station_name(external_name)
    existing = conn.execute(
        """
        SELECT id
        FROM station_name_mapping_proposals
        WHERE source_system = ?
          AND external_name = ?
          AND status = 'pending'
          AND (
                (project_id IS NULL AND ? IS NULL)
                OR project_id = ?
              )
        ORDER BY id DESC
        LIMIT 1
        """,
        (source_system, external_name, project_id, project_id),
    ).fetchone()
    if existing:
        return existing[0] if not hasattr(existing, "keys") else existing["id"]

    cursor = conn.execute(
        """
        INSERT INTO station_name_mapping_proposals (
            import_batch_id, project_id, source_system, external_name, normalized_name,
            candidate_station_id, confidence_score, raw_context_json, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            import_batch_id,
            project_id,
            source_system,
            external_name,
            normalized_name,
            candidate_station_id,
            confidence_score,
            json.dumps(raw_context, ensure_ascii=False),
        ),
    )
    return cursor.lastrowid


def enqueue_fault_review_item(
    conn,
    *,
    import_batch_id,
    project_id,
    source_type,
    source_record_key_candidate,
    raw_payload,
    issue_type,
    issue_detail,
):
    if not table_exists(conn, "fault_import_review_queue"):
        return None

    existing = conn.execute(
        """
        SELECT id
        FROM fault_import_review_queue
        WHERE import_batch_id = ?
          AND project_id = ?
          AND source_type = ?
          AND issue_type = ?
          AND (
                (source_record_key_candidate IS NULL AND ? IS NULL)
                OR source_record_key_candidate = ?
              )
          AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            import_batch_id,
            project_id,
            source_type,
            issue_type,
            source_record_key_candidate,
            source_record_key_candidate,
        ),
    ).fetchone()
    if existing:
        return existing[0] if not hasattr(existing, "keys") else existing["id"]

    cursor = conn.execute(
        """
        INSERT INTO fault_import_review_queue (
            import_batch_id, project_id, source_type, source_record_key_candidate,
            raw_payload_json, issue_type, issue_detail, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            import_batch_id,
            project_id,
            source_type,
            source_record_key_candidate,
            json.dumps(raw_payload, ensure_ascii=False),
            issue_type,
            issue_detail,
        ),
    )
    return cursor.lastrowid


def split_station_tokens(station_text):
    if not station_text:
        return []
    parts = re.split(r"[/、，,；;]+", str(station_text))
    return [part.strip() for part in parts if part and part.strip()]
