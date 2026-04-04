import json

from flask import Blueprint, jsonify, render_template, request

from admin import require_admin
from notification_runtime import dispatch_pending_escalations, table_exists as runtime_table_exists
from project_access import get_projects, get_project_by_code, table_exists
from utils import get_db


admin_notifications_bp = Blueprint("admin_notifications", __name__, url_prefix="/admin")

ALLOWED_EVENT_TYPES = {"fault_created", "fault_closed", "fault_escalated"}
ALLOWED_CHANNELS = {"sms", "wechat", "email", "webhook"}


def _feature_ready(db):
    required_tables = {
        "projects",
        "project_notification_policies",
        "project_notification_configs",
    }
    missing = [table_name for table_name in required_tables if not table_exists(db, table_name)]
    if missing:
        return jsonify({"error": "feature unavailable until migration is applied", "missing_tables": missing}), 409
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


def _serialize_policy_row(row):
    payload = dict(row)
    payload["is_active"] = bool(payload.get("is_active"))
    payload["notify_on_create"] = bool(payload.get("notify_on_create"))
    payload["notify_on_close"] = bool(payload.get("notify_on_close"))
    payload["quiet_hours"] = _parse_json_text(payload.get("quiet_hours_json"))
    return payload


def _serialize_config_row(row):
    payload = dict(row)
    payload["is_active"] = bool(payload.get("is_active"))
    return payload


def _serialize_dispatch_log_row(row):
    payload = dict(row)
    payload["payload"] = _parse_json_text(payload.get("payload_json"))
    return payload


def _ensure_policy(db, project_id):
    row = db.execute(
        """
        SELECT id, project_id, quiet_hours_json, notify_on_create, notify_on_close,
               escalate_after_minutes, escalation_target_config_id, is_active,
               created_at, updated_at
        FROM project_notification_policies
        WHERE project_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row:
        return row

    cursor = db.execute(
        """
        INSERT INTO project_notification_policies (
            project_id, quiet_hours_json, notify_on_create, notify_on_close,
            escalate_after_minutes, escalation_target_config_id, is_active
        )
        VALUES (?, NULL, 1, 1, NULL, NULL, 1)
        """,
        (project_id,),
    )
    return db.execute(
        """
        SELECT id, project_id, quiet_hours_json, notify_on_create, notify_on_close,
               escalate_after_minutes, escalation_target_config_id, is_active,
               created_at, updated_at
        FROM project_notification_policies
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()


def _load_policy_with_configs(db, project_id):
    policy = _ensure_policy(db, project_id)
    configs = db.execute(
        """
        SELECT c.*, p.code AS project_code, p.name AS project_name
        FROM project_notification_configs c
        JOIN project_notification_policies np ON np.id = c.policy_id
        JOIN projects p ON p.id = np.project_id
        WHERE c.policy_id = ?
        ORDER BY c.event_type, c.channel, c.id
        """,
        (policy["id"],),
    ).fetchall()
    return _serialize_policy_row(policy), [_serialize_config_row(row) for row in configs]


def _validate_event_type(event_type):
    value = str(event_type or "").strip()
    if value not in ALLOWED_EVENT_TYPES:
        raise ValueError("invalid event_type")
    return value


def _validate_channel(channel):
    value = str(channel or "").strip()
    if value not in ALLOWED_CHANNELS:
        raise ValueError("invalid channel")
    return value


def _validate_quiet_hours(value):
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ValueError("quiet_hours must be an object with start/end")
    start = str(value.get("start") or "").strip()
    end = str(value.get("end") or "").strip()
    if not start or not end:
        raise ValueError("quiet_hours.start and quiet_hours.end are required")
    return json.dumps({"start": start, "end": end}, ensure_ascii=False)


@admin_notifications_bp.route("/notification-center", methods=["GET"])
@require_admin
def notification_center_page():
    return render_template("admin_notifications.html")


@admin_notifications_bp.route("/project-notifications", methods=["GET"])
@require_admin
def list_project_notifications():
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    projects = get_projects(db, include_inactive=True)
    policies = []
    configs = []
    for project in projects:
        policy, policy_configs = _load_policy_with_configs(db, project["id"])
        policy["project_code"] = project["code"]
        policy["project_name"] = project["name"]
        policy["project_is_active"] = bool(project.get("is_active"))
        policies.append(policy)
        configs.extend(policy_configs)

    return jsonify(
        {
            "projects": projects,
            "policies": policies,
            "configs": configs,
            "allowed_event_types": sorted(ALLOWED_EVENT_TYPES),
            "allowed_channels": sorted(ALLOWED_CHANNELS),
        }
    )


@admin_notifications_bp.route("/project-notification-policies/<string:project_code>", methods=["PUT"])
@require_admin
def update_project_notification_policy(project_code):
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    project = get_project_by_code(db, project_code, include_inactive=True)
    if not project:
        return jsonify({"error": "project not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        quiet_hours_json = _validate_quiet_hours(data.get("quiet_hours"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    policy = _ensure_policy(db, project["id"])

    escalation_target_config_id = data.get("escalation_target_config_id")
    if escalation_target_config_id in ("", None):
        escalation_target_config_id = None
    elif not db.execute(
        "SELECT id FROM project_notification_configs WHERE id = ? AND policy_id = ?",
        (escalation_target_config_id, policy["id"]),
    ).fetchone():
        return jsonify({"error": "escalation_target_config_id must belong to the same project policy"}), 400

    db.execute(
        """
        UPDATE project_notification_policies
        SET quiet_hours_json = ?,
            notify_on_create = ?,
            notify_on_close = ?,
            escalate_after_minutes = ?,
            escalation_target_config_id = ?,
            is_active = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            quiet_hours_json,
            1 if data.get("notify_on_create", True) else 0,
            1 if data.get("notify_on_close", True) else 0,
            data.get("escalate_after_minutes"),
            escalation_target_config_id,
            1 if data.get("is_active", True) else 0,
            policy["id"],
        ),
    )
    db.commit()

    refreshed, configs = _load_policy_with_configs(db, project["id"])
    refreshed["project_code"] = project["code"]
    refreshed["project_name"] = project["name"]
    refreshed["project_is_active"] = bool(project.get("is_active"))
    return jsonify({"message": "notification policy updated", "policy": refreshed, "configs": configs})


@admin_notifications_bp.route("/project-notifications", methods=["POST"])
@require_admin
def create_project_notification_config():
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    data = request.get_json(silent=True) or {}
    policy_id = data.get("policy_id")
    if not policy_id:
        project_code = str(data.get("project_code") or "").strip()
        project = get_project_by_code(db, project_code, include_inactive=True)
        if not project:
            return jsonify({"error": "project not found"}), 404
        policy = _ensure_policy(db, project["id"])
        policy_id = policy["id"]
    else:
        policy = db.execute(
            """
            SELECT id, project_id FROM project_notification_policies
            WHERE id = ?
            """,
            (policy_id,),
        ).fetchone()
        if not policy:
            return jsonify({"error": "policy not found"}), 404

    try:
        event_type = _validate_event_type(data.get("event_type"))
        channel = _validate_channel(data.get("channel"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    target_value = str(data.get("target_value") or "").strip()
    if not target_value:
        return jsonify({"error": "target_value is required"}), 400

    cursor = db.execute(
        """
        INSERT INTO project_notification_configs (
            policy_id, event_type, channel, target_value, is_active,
            deduplication_window_minutes, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            policy_id,
            event_type,
            channel,
            target_value,
            1 if data.get("is_active", True) else 0,
            int(data.get("deduplication_window_minutes", 60)),
        ),
    )
    db.commit()

    row = db.execute(
        """
        SELECT c.*, p.code AS project_code, p.name AS project_name
        FROM project_notification_configs c
        JOIN project_notification_policies np ON np.id = c.policy_id
        JOIN projects p ON p.id = np.project_id
        WHERE c.id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify({"message": "notification config created", "config": _serialize_config_row(row)}), 201


@admin_notifications_bp.route("/project-notification-dispatch-logs", methods=["GET"])
@require_admin
def list_notification_dispatch_logs():
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    if not runtime_table_exists(db, "notification_dispatch_logs"):
        return jsonify({"total": 0, "limit": 0, "offset": 0, "logs": []})

    project_code = (request.args.get("project") or "").strip()
    event_type = (request.args.get("event_type") or "").strip()
    status = (request.args.get("status") or "").strip()
    limit = min(max(request.args.get("limit", default=20, type=int), 1), 200)
    offset = max(request.args.get("offset", default=0, type=int), 0)

    query = """
        SELECT
            l.*,
            p.code AS project_code,
            p.name AS project_name
        FROM notification_dispatch_logs l
        LEFT JOIN projects p ON p.id = l.project_id
        WHERE 1 = 1
    """
    params = []
    if project_code:
        query += " AND p.code = ?"
        params.append(project_code)
    if event_type:
        query += " AND l.event_type = ?"
        params.append(event_type)
    if status:
        query += " AND l.status = ?"
        params.append(status)

    total = db.execute(f"SELECT COUNT(*) FROM ({query}) AS filtered", params).fetchone()[0]
    rows = db.execute(
        query + " ORDER BY l.created_at DESC, l.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return jsonify(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "logs": [_serialize_dispatch_log_row(row) for row in rows],
        }
    )


@admin_notifications_bp.route("/project-notifications/maintenance", methods=["POST"])
@require_admin
def run_notification_maintenance():
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    data = request.get_json(silent=True) or {}
    apply_changes = bool(data.get("apply", True))
    report = dispatch_pending_escalations(db)
    if apply_changes:
        db.commit()
    else:
        db.rollback()

    return jsonify(
        {
            "message": "notification maintenance completed",
            "mode": "apply" if apply_changes else "dry-run",
            "report": report,
        }
    )


@admin_notifications_bp.route("/project-notifications/<int:config_id>", methods=["PUT"])
@require_admin
def update_project_notification_config(config_id):
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    row = db.execute(
        """
        SELECT c.*, np.project_id
        FROM project_notification_configs c
        JOIN project_notification_policies np ON np.id = c.policy_id
        WHERE c.id = ?
        """,
        (config_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "notification config not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        event_type = _validate_event_type(data.get("event_type", row["event_type"]))
        channel = _validate_channel(data.get("channel", row["channel"]))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    target_value = str(data.get("target_value", row["target_value"]) or "").strip()
    if not target_value:
        return jsonify({"error": "target_value is required"}), 400

    db.execute(
        """
        UPDATE project_notification_configs
        SET event_type = ?,
            channel = ?,
            target_value = ?,
            is_active = ?,
            deduplication_window_minutes = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            event_type,
            channel,
            target_value,
            1 if data.get("is_active", row["is_active"]) else 0,
            int(data.get("deduplication_window_minutes", row["deduplication_window_minutes"] or 60)),
            config_id,
        ),
    )
    db.commit()

    refreshed = db.execute(
        """
        SELECT c.*, p.code AS project_code, p.name AS project_name
        FROM project_notification_configs c
        JOIN project_notification_policies np ON np.id = c.policy_id
        JOIN projects p ON p.id = np.project_id
        WHERE c.id = ?
        """,
        (config_id,),
    ).fetchone()
    return jsonify({"message": "notification config updated", "config": _serialize_config_row(refreshed)})


@admin_notifications_bp.route("/project-notifications/<int:config_id>", methods=["DELETE"])
@require_admin
def delete_project_notification_config(config_id):
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    row = db.execute(
        """
        SELECT c.id, c.policy_id
        FROM project_notification_configs c
        WHERE c.id = ?
        """,
        (config_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "notification config not found"}), 404

    referenced = db.execute(
        """
        SELECT id
        FROM project_notification_policies
        WHERE escalation_target_config_id = ?
        LIMIT 1
        """,
        (config_id,),
    ).fetchone()
    if referenced:
        return jsonify({"error": "notification config is referenced by a policy escalation target"}), 409

    db.execute(
        """
        UPDATE project_notification_configs
        SET is_active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (config_id,),
    )
    db.commit()
    return jsonify({"message": "notification config deactivated", "config_id": config_id})
