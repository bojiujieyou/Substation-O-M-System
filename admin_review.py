import json

from flask import Blueprint, jsonify, render_template, request, session

from admin import require_admin
from import_review_support import get_columns, normalize_station_name
from project_access import table_exists
from utils import get_db


admin_review_bp = Blueprint("admin_review", __name__, url_prefix="/admin")


@admin_review_bp.route("/review-center", methods=["GET"])
@require_admin
def review_center_page():
    return render_template("admin_review.html")


def _require_tables(db, *table_names):
    missing = [table_name for table_name in table_names if not table_exists(db, table_name)]
    if missing:
        return jsonify(
            {
                "error": "feature unavailable until migration is applied",
                "missing_tables": missing,
            }
        ), 409
    return None


def _parse_json_text(value):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _json_response_row(row):
    payload = dict(row)
    if "raw_payload_json" in payload:
        payload["raw_payload"] = _parse_json_text(payload["raw_payload_json"])
    if "raw_context_json" in payload:
        payload["raw_context"] = _parse_json_text(payload["raw_context_json"])
    return payload


def _reviewer_id():
    return session.get("user_id")


def _append_review_note(existing_detail, note):
    note = (note or "").strip()
    if not note:
        return existing_detail
    if existing_detail:
        return f"{existing_detail}\n[review] {note}"
    return note


def _require_station(db, station_id):
    if not station_id:
        return None, (jsonify({"error": "station_id is required"}), 400)
    station = db.execute(
        "SELECT id, name FROM stations WHERE id = ?",
        (station_id,),
    ).fetchone()
    if not station:
        return None, (jsonify({"error": "station not found"}), 404)
    return station, None


def _update_queue_payload(db, item_id, payload):
    db.execute(
        """
        UPDATE fault_import_review_queue
        SET raw_payload_json = ?
        WHERE id = ?
        """,
        (json.dumps(payload, ensure_ascii=False), item_id),
    )


def _resolve_external_name_from_payload(payload):
    for key in ("external_name", "station_token", "station_text"):
        value = (payload or {}).get(key)
        if value:
            return str(value).strip()
    return ""


def _infer_fault_type(raw_payload):
    explicit = (raw_payload or {}).get("fault_type")
    if explicit:
        return str(explicit).strip()
    text = str((raw_payload or {}).get("content") or "")
    if any(token in text for token in ["网络", "断网", "掉线", "通信"]):
        return "网络故障"
    return "设备故障"


def _build_fault_description(raw_payload):
    description = (raw_payload or {}).get("description")
    if description:
        return str(description).strip()
    location = str((raw_payload or {}).get("location") or "").strip()
    content = str((raw_payload or {}).get("content") or "").strip()
    parts = []
    if content:
        parts.append(content)
    if location and location not in content:
        parts.append(f"地点: {location}")
    return " | ".join(parts)


def _coerce_optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_review_fault_status(item, raw_payload):
    raw_status = str((raw_payload or {}).get("status") or "").strip().lower()
    if raw_status in {"open", "handling", "closed"}:
        return raw_status
    if (raw_payload or {}).get("closed_at"):
        return "closed"
    if item["source_type"] == "import_worklog":
        return "closed"
    return "open"


def _resolve_review_slot_id(db, item, raw_payload, station_id):
    slot_id = _coerce_optional_int((raw_payload or {}).get("camera_slot_id"))
    if slot_id is None:
        slot_id = _coerce_optional_int((raw_payload or {}).get("slot_id"))
    if slot_id is None or not table_exists(db, "camera_slots"):
        return None

    row = db.execute(
        """
        SELECT id
        FROM camera_slots
        WHERE id = ?
          AND station_id = ?
          AND project_id = ?
        """,
        (slot_id, station_id, item["project_id"]),
    ).fetchone()
    if not row:
        return None
    return slot_id


def _create_fault_from_review_item(db, item, raw_payload, station_id):
    fault_report_columns = get_columns(db, "fault_reports")
    source_record_key = item["source_record_key_candidate"]
    if "source_record_key" in fault_report_columns and source_record_key:
        duplicate = db.execute(
            "SELECT id FROM fault_reports WHERE source_record_key = ?",
            (source_record_key,),
        ).fetchone()
        if duplicate:
            duplicate_id = duplicate["id"] if hasattr(duplicate, "keys") else duplicate[0]
            return None, (
                jsonify(
                    {
                        "error": "source_record_key already exists",
                        "existing_fault_id": duplicate_id,
                    }
                ),
                409,
            )

    created_at = (
        (raw_payload or {}).get("parsed_time")
        or (raw_payload or {}).get("created_at")
        or (raw_payload or {}).get("raw_time")
        or None
    )
    status = _normalize_review_fault_status(item, raw_payload)
    closed_at = (raw_payload or {}).get("closed_at") or (created_at if status == "closed" else None)
    handling_started_at = (
        (raw_payload or {}).get("handling_started_at")
        or (created_at if status == "handling" else None)
    )
    camera_slot_id = _resolve_review_slot_id(db, item, raw_payload, station_id)
    fault_type_code = str((raw_payload or {}).get("fault_type_code") or "").strip() or None
    fault_type_version_id = _coerce_optional_int((raw_payload or {}).get("fault_type_version_id"))
    project_device_code = str((raw_payload or {}).get("project_device_code") or "").strip() or None
    fault_type = _infer_fault_type(raw_payload)
    fault_type_label_snapshot = (
        str((raw_payload or {}).get("fault_type_label_snapshot") or "").strip()
        or str((raw_payload or {}).get("fault_type_label") or "").strip()
        or fault_type
    )
    description = _build_fault_description(raw_payload)

    insert_columns = [
        "station_id",
        "fault_type",
        "description",
        "reporter_name",
        "status",
        "created_at",
        "updated_at",
    ]
    values = [
        station_id,
        fault_type,
        description,
        "导入待确认处理",
        "closed",
        created_at,
        created_at,
    ]
    values[1] = fault_type_label_snapshot
    values[3] = str((raw_payload or {}).get("reporter_name") or "").strip() or "Review Queue Import"
    values[4] = status

    optional_fields = [
        ("system_type", (raw_payload or {}).get("system_type")),
        ("handler_name", (raw_payload or {}).get("handler_name")),
        ("closed_at", created_at),
        ("project_id", item["project_id"]),
        ("source_type", item["source_type"]),
        ("source_batch_id", str(item["import_batch_id"]) if item["import_batch_id"] is not None else None),
        ("source_record_key", source_record_key),
        ("fault_type_label_snapshot", fault_type),
        ("source_time_raw", (raw_payload or {}).get("raw_time")),
        ("source_timezone", (raw_payload or {}).get("source_timezone") or "Asia/Shanghai"),
    ]
    optional_fields = [
        (
            "closed_at",
            closed_at,
        ) if column_name == "closed_at" else (
            "fault_type_label_snapshot",
            fault_type_label_snapshot,
        ) if column_name == "fault_type_label_snapshot" else (
            column_name,
            value,
        )
        for column_name, value in optional_fields
    ]
    optional_fields.extend(
        [
            ("camera_slot_id", camera_slot_id),
            ("fault_type_code", fault_type_code),
            ("fault_type_version_id", fault_type_version_id),
            ("project_device_code", project_device_code),
            ("handling_started_at", handling_started_at),
        ]
    )
    for column_name, value in optional_fields:
        if column_name in fault_report_columns:
            insert_columns.append(column_name)
            values.append(value)

    placeholders = ", ".join(["?"] * len(insert_columns))
    cursor = db.execute(
        f"""
        INSERT INTO fault_reports ({", ".join(insert_columns)})
        VALUES ({placeholders})
        """,
        values,
    )
    return cursor.lastrowid, None


def _resolve_station_name_proposal(db, proposal_id):
    return db.execute(
        """
        SELECT p.*
        FROM station_name_mapping_proposals p
        WHERE p.id = ?
        """,
        (proposal_id,),
    ).fetchone()


def _resolve_import_review_item(db, item_id):
    return db.execute(
        """
        SELECT q.*
        FROM fault_import_review_queue q
        WHERE q.id = ?
        """,
        (item_id,),
    ).fetchone()


@admin_review_bp.route("/station-name-proposals", methods=["GET"])
@require_admin
def list_station_name_proposals():
    db = get_db()
    feature_error = _require_tables(
        db, "station_name_mapping_proposals", "station_external_names"
    )
    if feature_error:
        return feature_error

    status = (request.args.get("status") or "").strip()
    source_system = (request.args.get("source_system") or "").strip()
    project_code = (request.args.get("project") or "").strip()
    limit = min(max(request.args.get("limit", default=50, type=int), 1), 200)
    offset = max(request.args.get("offset", default=0, type=int), 0)

    query = """
        SELECT
            p.*,
            pr.code AS project_code,
            pr.name AS project_name,
            s.name AS candidate_station_name
        FROM station_name_mapping_proposals p
        LEFT JOIN projects pr ON pr.id = p.project_id
        LEFT JOIN stations s ON s.id = p.candidate_station_id
        WHERE 1 = 1
    """
    params = []
    if status:
        query += " AND p.status = ?"
        params.append(status)
    if source_system:
        query += " AND p.source_system = ?"
        params.append(source_system)
    if project_code:
        query += " AND pr.code = ?"
        params.append(project_code)

    total = db.execute(f"SELECT COUNT(*) FROM ({query}) AS filtered", params).fetchone()[0]
    rows = db.execute(
        query + " ORDER BY p.created_at DESC, p.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return jsonify(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "proposals": [_json_response_row(row) for row in rows],
        }
    )


@admin_review_bp.route("/station-name-proposals/<int:proposal_id>", methods=["GET"])
@require_admin
def get_station_name_proposal_detail(proposal_id):
    db = get_db()
    feature_error = _require_tables(
        db, "station_name_mapping_proposals", "station_external_names"
    )
    if feature_error:
        return feature_error

    row = db.execute(
        """
        SELECT
            p.*,
            pr.code AS project_code,
            pr.name AS project_name,
            s.name AS candidate_station_name
        FROM station_name_mapping_proposals p
        LEFT JOIN projects pr ON pr.id = p.project_id
        LEFT JOIN stations s ON s.id = p.candidate_station_id
        WHERE p.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "proposal not found"}), 404
    return jsonify({"proposal": _json_response_row(row)})


@admin_review_bp.route("/station-name-proposals/<int:proposal_id>/approve", methods=["POST"])
@require_admin
def approve_station_name_proposal(proposal_id):
    db = get_db()
    feature_error = _require_tables(
        db, "station_name_mapping_proposals", "station_external_names"
    )
    if feature_error:
        return feature_error

    proposal = _resolve_station_name_proposal(db, proposal_id)
    if not proposal:
        return jsonify({"error": "proposal not found"}), 404
    if proposal["status"] != "pending":
        return jsonify({"error": "only pending proposals can be approved"}), 409

    data = request.get_json(silent=True) or {}
    station_id = data.get("station_id") or proposal["candidate_station_id"]
    if not station_id:
        return jsonify({"error": "station_id is required"}), 400

    station = db.execute("SELECT id, name FROM stations WHERE id = ?", (station_id,)).fetchone()
    if not station:
        return jsonify({"error": "station not found"}), 404

    is_primary = 1 if data.get("is_primary") else 0
    db.execute(
        """
        INSERT INTO station_external_names (
            station_id, source_system, external_name, normalized_name, is_primary
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_system, external_name) DO UPDATE SET
            station_id = excluded.station_id,
            normalized_name = excluded.normalized_name,
            is_primary = excluded.is_primary
        """,
        (
            station_id,
            proposal["source_system"],
            proposal["external_name"],
            proposal["normalized_name"],
            is_primary,
        ),
    )
    db.execute(
        """
        UPDATE station_name_mapping_proposals
        SET status = 'approved',
            candidate_station_id = ?,
            reviewer_id = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (station_id, _reviewer_id(), proposal_id),
    )
    db.commit()
    return jsonify(
        {
            "message": "proposal approved",
            "proposal_id": proposal_id,
            "station_id": station["id"],
            "station_name": station["name"],
        }
    )


@admin_review_bp.route("/station-name-proposals/<int:proposal_id>/reject", methods=["POST"])
@require_admin
def reject_station_name_proposal(proposal_id):
    db = get_db()
    feature_error = _require_tables(db, "station_name_mapping_proposals")
    if feature_error:
        return feature_error

    proposal = _resolve_station_name_proposal(db, proposal_id)
    if not proposal:
        return jsonify({"error": "proposal not found"}), 404
    if proposal["status"] != "pending":
        return jsonify({"error": "only pending proposals can be rejected"}), 409

    db.execute(
        """
        UPDATE station_name_mapping_proposals
        SET status = 'rejected',
            reviewer_id = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (_reviewer_id(), proposal_id),
    )
    db.commit()
    return jsonify({"message": "proposal rejected", "proposal_id": proposal_id})


@admin_review_bp.route("/station-name-proposals/batch-reject", methods=["POST"])
@require_admin
def batch_reject_station_name_proposals():
    db = get_db()
    feature_error = _require_tables(db, "station_name_mapping_proposals")
    if feature_error:
        return feature_error

    data = request.get_json(silent=True) or {}
    proposal_ids = [int(item) for item in data.get("proposal_ids", []) if str(item).strip()]
    if not proposal_ids:
        return jsonify({"error": "proposal_ids is required"}), 400

    placeholders = ", ".join(["?"] * len(proposal_ids))
    cursor = db.execute(
        f"""
        UPDATE station_name_mapping_proposals
        SET status = 'rejected',
            reviewer_id = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE status = 'pending'
          AND id IN ({placeholders})
        """,
        [_reviewer_id(), *proposal_ids],
    )
    db.commit()
    return jsonify({"message": "batch reject completed", "updated_count": cursor.rowcount})


@admin_review_bp.route("/station-name-proposals/batch-approve", methods=["POST"])
@require_admin
def batch_approve_station_name_proposals():
    db = get_db()
    feature_error = _require_tables(
        db, "station_name_mapping_proposals", "station_external_names"
    )
    if feature_error:
        return feature_error

    data = request.get_json(silent=True) or {}
    proposal_ids = [int(item) for item in data.get("proposal_ids", []) if str(item).strip()]
    default_station_id = data.get("station_id")
    is_primary = 1 if data.get("is_primary") else 0
    if not proposal_ids:
        return jsonify({"error": "proposal_ids is required"}), 400

    updated_count = 0
    for proposal_id in proposal_ids:
        proposal = _resolve_station_name_proposal(db, proposal_id)
        if not proposal or proposal["status"] != "pending":
            continue
        station_id = default_station_id or proposal["candidate_station_id"]
        if not station_id:
            continue
        station, error = _require_station(db, station_id)
        if error:
            continue
        db.execute(
            """
            INSERT INTO station_external_names (
                station_id, source_system, external_name, normalized_name, is_primary
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_system, external_name) DO UPDATE SET
                station_id = excluded.station_id,
                normalized_name = excluded.normalized_name,
                is_primary = excluded.is_primary
            """,
            (
                station["id"],
                proposal["source_system"],
                proposal["external_name"],
                proposal["normalized_name"],
                is_primary,
            ),
        )
        db.execute(
            """
            UPDATE station_name_mapping_proposals
            SET status = 'approved',
                candidate_station_id = ?,
                reviewer_id = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (station["id"], _reviewer_id(), proposal_id),
        )
        updated_count += 1

    db.commit()
    return jsonify({"message": "batch approve completed", "updated_count": updated_count})


@admin_review_bp.route("/import-review-queue", methods=["GET"])
@require_admin
def list_import_review_queue():
    db = get_db()
    feature_error = _require_tables(db, "fault_import_review_queue", "import_batches")
    if feature_error:
        return feature_error

    status = (request.args.get("status") or "").strip()
    issue_type = (request.args.get("issue_type") or "").strip()
    project_code = (request.args.get("project") or "").strip()
    limit = min(max(request.args.get("limit", default=50, type=int), 1), 200)
    offset = max(request.args.get("offset", default=0, type=int), 0)

    query = """
        SELECT
            q.*,
            pr.code AS project_code,
            pr.name AS project_name
        FROM fault_import_review_queue q
        LEFT JOIN projects pr ON pr.id = q.project_id
        WHERE 1 = 1
    """
    params = []
    if status:
        query += " AND q.status = ?"
        params.append(status)
    if issue_type:
        query += " AND q.issue_type = ?"
        params.append(issue_type)
    if project_code:
        query += " AND pr.code = ?"
        params.append(project_code)

    total = db.execute(f"SELECT COUNT(*) FROM ({query}) AS filtered", params).fetchone()[0]
    rows = db.execute(
        query + " ORDER BY q.created_at DESC, q.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return jsonify(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_json_response_row(row) for row in rows],
        }
    )


@admin_review_bp.route("/import-review-queue/<int:item_id>", methods=["GET"])
@require_admin
def get_import_review_item_detail(item_id):
    db = get_db()
    feature_error = _require_tables(db, "fault_import_review_queue", "import_batches")
    if feature_error:
        return feature_error

    row = db.execute(
        """
        SELECT
            q.*,
            pr.code AS project_code,
            pr.name AS project_name
        FROM fault_import_review_queue q
        LEFT JOIN projects pr ON pr.id = q.project_id
        WHERE q.id = ?
        """,
        (item_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "review item not found"}), 404
    return jsonify({"item": _json_response_row(row)})


@admin_review_bp.route("/import-review-queue/<int:item_id>/approve-import", methods=["POST"])
@require_admin
def approve_import_review_item(item_id):
    db = get_db()
    feature_error = _require_tables(db, "fault_import_review_queue", "fault_reports")
    if feature_error:
        return feature_error

    item = _resolve_import_review_item(db, item_id)
    if not item:
        return jsonify({"error": "review item not found"}), 404
    if item["status"] != "pending":
        return jsonify({"error": "only pending review items can be approved"}), 409

    payload = request.get_json(silent=True) or {}
    raw_payload = _parse_json_text(item["raw_payload_json"]) or {}
    station_id = (
        payload.get("station_id")
        or raw_payload.get("assigned_station_id")
        or raw_payload.get("station_id")
        or raw_payload.get("candidate_station_id")
    )
    station, error = _require_station(db, station_id)
    if error:
        return error

    fault_id, fault_error = _create_fault_from_review_item(
        db,
        item,
        raw_payload,
        station["id"],
    )
    if fault_error:
        db.rollback()
        return fault_error

    note = _append_review_note(item["issue_detail"], payload.get("note", ""))
    db.execute(
        """
        UPDATE fault_import_review_queue
        SET status = 'approved',
            resolved_fault_id = ?,
            issue_detail = ?,
            reviewer_id = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (fault_id, note, _reviewer_id(), item_id),
    )
    db.commit()
    return jsonify(
        {
            "message": "review item approved and imported",
            "item_id": item_id,
            "fault_id": fault_id,
        }
    )


@admin_review_bp.route("/import-review-queue/<int:item_id>/merge-existing", methods=["POST"])
@require_admin
def merge_import_review_item(item_id):
    db = get_db()
    feature_error = _require_tables(db, "fault_import_review_queue", "fault_reports")
    if feature_error:
        return feature_error

    item = _resolve_import_review_item(db, item_id)
    if not item:
        return jsonify({"error": "review item not found"}), 404
    if item["status"] != "pending":
        return jsonify({"error": "only pending review items can be merged"}), 409

    payload = request.get_json(silent=True) or {}
    existing_fault_id = payload.get("fault_id")
    if not existing_fault_id:
        return jsonify({"error": "fault_id is required"}), 400

    existing_fault = db.execute(
        "SELECT id FROM fault_reports WHERE id = ?",
        (existing_fault_id,),
    ).fetchone()
    if not existing_fault:
        return jsonify({"error": "target fault not found"}), 404

    note = _append_review_note(item["issue_detail"], payload.get("note", "merged into existing fault"))
    db.execute(
        """
        UPDATE fault_import_review_queue
        SET status = 'approved',
            resolved_fault_id = ?,
            issue_detail = ?,
            reviewer_id = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (existing_fault_id, note, _reviewer_id(), item_id),
    )
    db.commit()
    return jsonify(
        {
            "message": "review item merged into existing fault",
            "item_id": item_id,
            "fault_id": existing_fault_id,
        }
    )


@admin_review_bp.route("/import-review-queue/<int:item_id>/reject", methods=["POST"])
@require_admin
def reject_import_review_item(item_id):
    db = get_db()
    feature_error = _require_tables(db, "fault_import_review_queue")
    if feature_error:
        return feature_error

    item = _resolve_import_review_item(db, item_id)
    if not item:
        return jsonify({"error": "review item not found"}), 404
    if item["status"] != "pending":
        return jsonify({"error": "only pending review items can be rejected"}), 409

    payload = request.get_json(silent=True) or {}
    issue_detail = item["issue_detail"]
    if payload.get("reason"):
        reason = str(payload["reason"]).strip()
        issue_detail = f"{issue_detail}\n[review] {reason}".strip() if issue_detail else reason

    db.execute(
        """
        UPDATE fault_import_review_queue
        SET status = 'rejected',
            issue_detail = ?,
            reviewer_id = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (issue_detail, _reviewer_id(), item_id),
    )
    db.commit()
    return jsonify({"message": "review item rejected", "item_id": item_id})


@admin_review_bp.route("/import-review-queue/batch-reject", methods=["POST"])
@require_admin
def batch_reject_import_review_queue():
    db = get_db()
    feature_error = _require_tables(db, "fault_import_review_queue")
    if feature_error:
        return feature_error

    data = request.get_json(silent=True) or {}
    item_ids = [int(item) for item in data.get("item_ids", []) if str(item).strip()]
    if not item_ids:
        return jsonify({"error": "item_ids is required"}), 400

    placeholders = ", ".join(["?"] * len(item_ids))
    cursor = db.execute(
        f"""
        UPDATE fault_import_review_queue
        SET status = 'rejected',
            reviewer_id = ?,
            reviewed_at = CURRENT_TIMESTAMP
        WHERE status = 'pending'
          AND id IN ({placeholders})
        """,
        [_reviewer_id(), *item_ids],
    )
    db.commit()
    return jsonify({"message": "batch reject completed", "updated_count": cursor.rowcount})


@admin_review_bp.route("/import-review-queue/batch-assign-station", methods=["POST"])
@require_admin
def batch_assign_station_for_review_queue():
    db = get_db()
    feature_error = _require_tables(db, "fault_import_review_queue")
    if feature_error:
        return feature_error

    data = request.get_json(silent=True) or {}
    item_ids = [int(item) for item in data.get("item_ids", []) if str(item).strip()]
    station_id = data.get("station_id")
    if not item_ids:
        return jsonify({"error": "item_ids is required"}), 400
    station, error = _require_station(db, station_id)
    if error:
        return error

    updated_count = 0
    for item_id in item_ids:
        item = _resolve_import_review_item(db, item_id)
        if not item or item["status"] != "pending":
            continue
        payload = _parse_json_text(item["raw_payload_json"]) or {}
        payload["assigned_station_id"] = station["id"]
        payload["assigned_station_name"] = station["name"]
        _update_queue_payload(db, item_id, payload)
        db.execute(
            """
            UPDATE fault_import_review_queue
            SET issue_detail = ?
            WHERE id = ?
            """,
            (_append_review_note(item["issue_detail"], f"assigned station {station['name']}"), item_id),
        )
        updated_count += 1

    db.commit()
    return jsonify(
        {
            "message": "batch station assignment completed",
            "updated_count": updated_count,
            "station_id": station["id"],
            "station_name": station["name"],
        }
    )


@admin_review_bp.route("/import-review-queue/batch-apply-mapping", methods=["POST"])
@require_admin
def batch_apply_mapping_for_review_queue():
    db = get_db()
    feature_error = _require_tables(
        db, "fault_import_review_queue", "station_external_names"
    )
    if feature_error:
        return feature_error

    data = request.get_json(silent=True) or {}
    item_ids = [int(item) for item in data.get("item_ids", []) if str(item).strip()]
    station_id = data.get("station_id")
    source_system = (data.get("source_system") or "").strip()
    is_primary = 1 if data.get("is_primary") else 0
    if not item_ids:
        return jsonify({"error": "item_ids is required"}), 400
    station, error = _require_station(db, station_id)
    if error:
        return error

    mapped_count = 0
    updated_count = 0
    for item_id in item_ids:
        item = _resolve_import_review_item(db, item_id)
        if not item or item["status"] != "pending":
            continue
        payload = _parse_json_text(item["raw_payload_json"]) or {}
        external_name = _resolve_external_name_from_payload(payload)
        if not external_name:
            continue
        effective_source_system = source_system or payload.get("source_system") or "review_queue"
        normalized_name = payload.get("normalized_station_name") or external_name
        db.execute(
            """
            INSERT INTO station_external_names (
                station_id, source_system, external_name, normalized_name, is_primary
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_system, external_name) DO UPDATE SET
                station_id = excluded.station_id,
                normalized_name = excluded.normalized_name,
                is_primary = excluded.is_primary
            """,
            (
                station["id"],
                effective_source_system,
                external_name,
                normalize_station_name(normalized_name),
                is_primary,
            ),
        )
        payload["assigned_station_id"] = station["id"]
        payload["assigned_station_name"] = station["name"]
        payload["source_system"] = effective_source_system
        _update_queue_payload(db, item_id, payload)
        db.execute(
            """
            UPDATE fault_import_review_queue
            SET issue_detail = ?
            WHERE id = ?
            """,
            (_append_review_note(item["issue_detail"], f"applied mapping {external_name} -> {station['name']}"), item_id),
        )
        mapped_count += 1
        updated_count += 1

    db.commit()
    return jsonify(
        {
            "message": "batch mapping apply completed",
            "updated_count": updated_count,
            "mapped_count": mapped_count,
            "station_id": station["id"],
            "station_name": station["name"],
        }
    )
