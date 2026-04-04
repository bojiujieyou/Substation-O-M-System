import re

from flask import Blueprint, jsonify, render_template, request

from admin import require_admin
from project_access import get_project_by_code, get_projects, table_exists
from utils import get_db


admin_projects_bp = Blueprint("admin_projects", __name__, url_prefix="/admin")

PROJECT_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
IMMUTABLE_FIELDS = {"code", "name", "short_name"}


def _feature_ready(db):
    if not table_exists(db, "projects"):
        return jsonify({"error": "feature unavailable until migration is applied", "missing_tables": ["projects"]}), 409
    return None


def _normalize_project_payload(data, *, creating: bool):
    payload = data if isinstance(data, dict) else {}

    code = str(payload.get("code") or "").strip()
    name = str(payload.get("name") or "").strip()
    short_name = str(payload.get("short_name") or "").strip()
    color = str(payload.get("color") or "#1a73e8").strip()
    sort_order = payload.get("sort_order")

    if creating:
        if not code:
            raise ValueError("code is required")
        if not PROJECT_CODE_RE.match(code):
            raise ValueError("code must start with a lowercase letter and contain only lowercase letters, numbers, '_' or '-'")
        if not name:
            raise ValueError("name is required")
        if not short_name:
            raise ValueError("short_name is required")

    if color and not HEX_COLOR_RE.match(color):
        raise ValueError("color must be a hex value like #1a73e8")

    normalized = {
        "code": code,
        "name": name,
        "short_name": short_name,
        "color": color,
        "is_active": bool(payload.get("is_active", True)),
    }
    if sort_order in ("", None):
        normalized["sort_order"] = None
    else:
        normalized["sort_order"] = int(sort_order)
    return normalized


@admin_projects_bp.route("/project-center", methods=["GET"])
@require_admin
def project_center_page():
    return render_template("admin_projects.html")


@admin_projects_bp.route("/projects", methods=["GET"])
@require_admin
def list_projects_admin():
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error
    return jsonify({"projects": get_projects(db, include_inactive=True)})


@admin_projects_bp.route("/projects", methods=["POST"])
@require_admin
def create_project_admin():
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    try:
        payload = _normalize_project_payload(request.get_json(silent=True) or {}, creating=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if get_project_by_code(db, payload["code"], include_inactive=True):
        return jsonify({"error": "project code already exists"}), 409

    sort_order = payload["sort_order"]
    if sort_order is None:
        row = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_sort_order FROM projects").fetchone()
        sort_order = row["next_sort_order"] if hasattr(row, "keys") else row[0]

    db.execute(
        """
        INSERT INTO projects (code, name, short_name, color, sort_order, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload["code"],
            payload["name"],
            payload["short_name"],
            payload["color"],
            sort_order,
            1 if payload["is_active"] else 0,
        ),
    )
    db.commit()

    project = get_project_by_code(db, payload["code"], include_inactive=True)
    return jsonify({"message": "project created", "project": project}), 201


@admin_projects_bp.route("/projects/<string:project_code>", methods=["PUT"])
@require_admin
def update_project_admin(project_code):
    db = get_db()
    feature_error = _feature_ready(db)
    if feature_error:
        return feature_error

    project = get_project_by_code(db, project_code, include_inactive=True)
    if not project:
        return jsonify({"error": "project not found"}), 404

    payload = request.get_json(silent=True) or {}
    forbidden = sorted(IMMUTABLE_FIELDS.intersection(payload.keys()))
    if forbidden:
        return jsonify({"error": f"immutable fields cannot be updated: {', '.join(forbidden)}"}), 400

    try:
        normalized = _normalize_project_payload(payload, creating=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    db.execute(
        """
        UPDATE projects
        SET color = ?,
            sort_order = COALESCE(?, sort_order),
            is_active = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE code = ?
        """,
        (
            normalized["color"] or project["color"],
            normalized["sort_order"],
            1 if normalized["is_active"] else 0,
            project_code,
        ),
    )
    db.commit()

    refreshed = get_project_by_code(db, project_code, include_inactive=True)
    return jsonify({"message": "project updated", "project": refreshed})
