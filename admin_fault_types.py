import json

from flask import Blueprint, jsonify, render_template, request

from admin import require_admin
from project_access import get_project_by_code, table_exists
from utils import get_db


admin_fault_types_bp = Blueprint("admin_fault_types", __name__, url_prefix="/admin")


def _feature_ready(db):
    required_tables = {"projects", "project_fault_type_versions", "project_fault_types"}
    missing = [name for name in required_tables if not table_exists(db, name)]
    if missing:
        return jsonify({"error": "feature unavailable until migration is applied", "missing_tables": missing}), 409
    return None


def _load_version_types(db, version_id):
    rows = db.execute(
        """
        SELECT id, version_id, type_code, type_label, semantic_group, sort_order, is_active, created_at
        FROM project_fault_types
        WHERE version_id = ?
        ORDER BY sort_order, id
        """,
        (version_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _published_version_row(db, project_id, *, exclude_version_id=None):
    query = """
        SELECT id, project_id, version, description, is_published, published_at, created_at
        FROM project_fault_type_versions
        WHERE project_id = ?
          AND is_published = 1
    """
    params = [project_id]
    if exclude_version_id is not None:
        query += " AND id != ?"
        params.append(exclude_version_id)
    query += " ORDER BY version DESC LIMIT 1"
    return db.execute(query, params).fetchone()


def _normalize_types_for_compare(type_rows):
    normalized = {}
    for row in type_rows:
        normalized[row["type_code"]] = {
            "type_code": row["type_code"],
            "type_label": row["type_label"],
            "semantic_group": row.get("semantic_group") or row["type_code"],
            "sort_order": row.get("sort_order") or 0,
            "is_active": int(row.get("is_active", 1)),
        }
    return normalized


def _build_diff_items(previous_types, candidate_types):
    previous_map = _normalize_types_for_compare(previous_types)
    candidate_map = _normalize_types_for_compare(candidate_types)
    diff_items = []

    for type_code in sorted(set(previous_map) | set(candidate_map)):
        previous = previous_map.get(type_code)
        candidate = candidate_map.get(type_code)
        if previous is None and candidate is not None:
            diff_items.append(
                {
                    "key": f"type:{type_code}",
                    "change_type": "added",
                    "type_code": type_code,
                    "previous": None,
                    "candidate": candidate,
                    "allowed_decisions": ["new_type"],
                }
            )
            continue
        if candidate is None and previous is not None:
            diff_items.append(
                {
                    "key": f"removed:{type_code}",
                    "change_type": "removed",
                    "type_code": type_code,
                    "previous": previous,
                    "candidate": None,
                    "allowed_decisions": ["removed_type"],
                }
            )
            continue

        changed_fields = {}
        for field_name in ("type_label", "semantic_group", "sort_order", "is_active"):
            if previous[field_name] != candidate[field_name]:
                changed_fields[field_name] = {
                    "previous": previous[field_name],
                    "candidate": candidate[field_name],
                }
        if changed_fields:
            diff_items.append(
                {
                    "key": f"type:{type_code}",
                    "change_type": "changed",
                    "type_code": type_code,
                    "previous": previous,
                    "candidate": candidate,
                    "changed_fields": changed_fields,
                    "allowed_decisions": ["semantic_continuity", "semantic_changed"],
                }
            )
    return diff_items


def _serialize_version(db, row, *, include_types=True, include_diff=True):
    payload = dict(row)
    payload["is_published"] = bool(payload.get("is_published"))
    payload["editable"] = not payload["is_published"]
    if include_types:
        payload["types"] = _load_version_types(db, payload["id"])
    if include_diff:
        previous_published = _published_version_row(
            db,
            payload["project_id"],
            exclude_version_id=payload["id"],
        )
        previous_types = _load_version_types(db, previous_published["id"]) if previous_published else []
        candidate_types = payload.get("types") if include_types else _load_version_types(db, payload["id"])
        payload["diff_items"] = _build_diff_items(previous_types, candidate_types)
        payload["diff_summary"] = {
            "added": sum(1 for item in payload["diff_items"] if item["change_type"] == "added"),
            "removed": sum(1 for item in payload["diff_items"] if item["change_type"] == "removed"),
            "changed": sum(1 for item in payload["diff_items"] if item["change_type"] == "changed"),
        }
    return payload


def _parse_fault_types(raw_types):
    if not isinstance(raw_types, list):
        raise ValueError("fault_types must be a list")
    parsed = []
    seen_codes = set()
    for index, item in enumerate(raw_types, start=1):
        if not isinstance(item, dict):
            raise ValueError("fault_types entries must be objects")
        type_code = str(item.get("type_code") or "").strip()
        type_label = str(item.get("type_label") or "").strip()
        semantic_group = str(item.get("semantic_group") or "").strip() or type_code
        if not type_code:
            raise ValueError(f"type_code is required for row {index}")
        if not type_label:
            raise ValueError(f"type_label is required for row {index}")
        if type_code in seen_codes:
            raise ValueError(f"duplicate type_code: {type_code}")
        seen_codes.add(type_code)
        parsed.append(
            {
                "type_code": type_code,
                "type_label": type_label,
                "semantic_group": semantic_group,
                "sort_order": int(item.get("sort_order", index)),
                "is_active": 1 if item.get("is_active", True) else 0,
            }
        )
    return parsed


def _insert_fault_types(db, version_id, fault_types):
    for item in fault_types:
        db.execute(
            """
            INSERT INTO project_fault_types (
                version_id, type_code, type_label, semantic_group, sort_order, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                item["type_code"],
                item["type_label"],
                item["semantic_group"],
                item["sort_order"],
                item["is_active"],
            ),
        )


def _validate_publish_confirmations(diff_items, confirmations):
    confirmation_map = confirmations if isinstance(confirmations, dict) else {}
    for item in diff_items:
        decision = confirmation_map.get(item["key"])
        if decision not in item["allowed_decisions"]:
            raise ValueError(f"missing or invalid confirmation for {item['key']}")

        if item["change_type"] == "changed":
            previous_group = item["previous"]["semantic_group"]
            candidate_group = item["candidate"]["semantic_group"]
            if decision == "semantic_continuity" and previous_group != candidate_group:
                raise ValueError(f"{item['type_code']} must keep semantic_group for semantic continuity")
            if decision == "semantic_changed" and previous_group == candidate_group:
                raise ValueError(f"{item['type_code']} must use a new semantic_group for semantic change")


@admin_fault_types_bp.route("/fault-type-center", methods=["GET"])
@require_admin
def fault_type_center_page():
    return render_template("admin_fault_types.html")


@admin_fault_types_bp.route("/projects/<string:project_code>/fault-type-versions", methods=["GET"])
@require_admin
def list_fault_type_versions(project_code):
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    project = get_project_by_code(db, project_code, include_inactive=True)
    if not project:
        return jsonify({"error": "project not found"}), 404

    rows = db.execute(
        """
        SELECT id, project_id, version, description, is_published, published_at, created_at
        FROM project_fault_type_versions
        WHERE project_id = ?
        ORDER BY version DESC, id DESC
        """,
        (project["id"],),
    ).fetchall()
    versions = [_serialize_version(db, row) for row in rows]
    return jsonify({"project": project, "versions": versions})


@admin_fault_types_bp.route("/projects/<string:project_code>/fault-type-versions", methods=["POST"])
@require_admin
def create_fault_type_version(project_code):
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    project = get_project_by_code(db, project_code, include_inactive=True)
    if not project:
        return jsonify({"error": "project not found"}), 404

    data = request.get_json(silent=True) or {}
    description = str(data.get("description") or "").strip()
    copy_from_version_id = data.get("copy_from_version_id")

    if data.get("fault_types") is not None:
        try:
            fault_types = _parse_fault_types(data.get("fault_types"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        if copy_from_version_id is None:
            published = _published_version_row(db, project["id"])
            copy_from_version_id = published["id"] if published else None
        fault_types = _load_version_types(db, copy_from_version_id) if copy_from_version_id else []
        fault_types = _parse_fault_types(fault_types)

    next_version = (
        db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM project_fault_type_versions WHERE project_id = ?",
            (project["id"],),
        ).fetchone()[0]
        or 1
    )
    cursor = db.execute(
        """
        INSERT INTO project_fault_type_versions (project_id, version, description, is_published)
        VALUES (?, ?, ?, 0)
        """,
        (project["id"], next_version, description),
    )
    version_id = cursor.lastrowid
    _insert_fault_types(db, version_id, fault_types)
    db.commit()

    row = db.execute(
        """
        SELECT id, project_id, version, description, is_published, published_at, created_at
        FROM project_fault_type_versions
        WHERE id = ?
        """,
        (version_id,),
    ).fetchone()
    return jsonify(
        {
            "message": "fault type version created",
            "project": project,
            "version": _serialize_version(db, row),
        }
    ), 201


@admin_fault_types_bp.route("/projects/<string:project_code>/fault-type-versions/<int:version_id>/publish", methods=["POST"])
@require_admin
def publish_fault_type_version(project_code, version_id):
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    project = get_project_by_code(db, project_code, include_inactive=True)
    if not project:
        return jsonify({"error": "project not found"}), 404

    version_row = db.execute(
        """
        SELECT id, project_id, version, description, is_published, published_at, created_at
        FROM project_fault_type_versions
        WHERE id = ? AND project_id = ?
        """,
        (version_id, project["id"]),
    ).fetchone()
    if not version_row:
        return jsonify({"error": "fault type version not found"}), 404
    if version_row["is_published"]:
        return jsonify({"error": "published version cannot be published again"}), 409

    candidate_types = _load_version_types(db, version_id)
    if not candidate_types:
        return jsonify({"error": "cannot publish an empty fault type version"}), 400

    previous_published = _published_version_row(db, project["id"], exclude_version_id=version_id)
    previous_types = _load_version_types(db, previous_published["id"]) if previous_published else []
    diff_items = _build_diff_items(previous_types, candidate_types)

    data = request.get_json(silent=True) or {}
    try:
        _validate_publish_confirmations(diff_items, data.get("confirmations") or {})
    except ValueError as exc:
        return jsonify({"error": str(exc), "diff_items": diff_items}), 400

    db.execute(
        "UPDATE project_fault_type_versions SET is_published = 0 WHERE project_id = ?",
        (project["id"],),
    )
    db.execute(
        """
        UPDATE project_fault_type_versions
        SET is_published = 1,
            published_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (version_id,),
    )
    db.execute(
        "UPDATE projects SET fault_type_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (version_id, project["id"]),
    )
    db.commit()

    refreshed = db.execute(
        """
        SELECT id, project_id, version, description, is_published, published_at, created_at
        FROM project_fault_type_versions
        WHERE id = ?
        """,
        (version_id,),
    ).fetchone()
    return jsonify(
        {
            "message": "fault type version published",
            "project": get_project_by_code(db, project_code, include_inactive=True),
            "version": _serialize_version(db, refreshed),
        }
    )
