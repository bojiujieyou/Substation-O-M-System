"""Project visibility and scope helpers."""

from __future__ import annotations

from typing import Any


LEGACY_PROJECT = {
    "id": 1,
    "code": "unified",
    "name": "统一平台",
    "short_name": "统一",
    "color": "#1a73e8",
    "fault_type_version_id": None,
    "sort_order": 1,
    "is_active": 1,
    "current_fault_type_version": None,
}


def table_exists(db, table_name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def projects_enabled(db) -> bool:
    return table_exists(db, "projects")


def project_scopes_enabled(db) -> bool:
    return table_exists(db, "user_project_scopes")


def fault_type_versions_enabled(db) -> bool:
    return table_exists(db, "project_fault_type_versions")


def _serialize_project_row(row) -> dict[str, Any]:
    project = dict(row)
    if "is_active" in project:
        project["is_active"] = bool(project.get("is_active"))
    current_version = None
    if project.get("fault_type_version_id"):
        current_version = {
            "id": project.get("fault_type_version_id"),
            "version": project.get("current_fault_type_version_number"),
            "description": project.get("current_fault_type_version_description"),
            "is_published": bool(project.get("current_fault_type_is_published")),
            "published_at": project.get("current_fault_type_published_at"),
        }
    project["current_fault_type_version"] = current_version
    for key in (
        "current_fault_type_version_number",
        "current_fault_type_version_description",
        "current_fault_type_is_published",
        "current_fault_type_published_at",
    ):
        project.pop(key, None)
    return project


def _legacy_project(can_write: bool = False) -> dict[str, Any]:
    project = dict(LEGACY_PROJECT)
    project["can_write"] = bool(can_write)
    return project


def get_projects(db, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not projects_enabled(db):
        return [_legacy_project()]

    if fault_type_versions_enabled(db):
        query = """
            SELECT
                p.*,
                v.version AS current_fault_type_version_number,
                v.description AS current_fault_type_version_description,
                v.is_published AS current_fault_type_is_published,
                v.published_at AS current_fault_type_published_at
            FROM projects p
            LEFT JOIN project_fault_type_versions v
              ON v.id = p.fault_type_version_id
        """
    else:
        query = "SELECT p.* FROM projects p"

    params = []
    if not include_inactive:
        query += " WHERE p.is_active = 1"
    query += " ORDER BY p.sort_order, p.id"

    return [_serialize_project_row(row) for row in db.execute(query, params).fetchall()]


def get_visible_projects(
    db,
    *,
    user_id: int | None,
    role: str | None,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    if not projects_enabled(db):
        return [_legacy_project(can_write=role in ("admin", "operator"))]

    if role == "admin":
        projects = get_projects(db, include_inactive=include_inactive)
        for project in projects:
            project["can_write"] = True
        return projects

    if not user_id or not project_scopes_enabled(db):
        return []

    if fault_type_versions_enabled(db):
        query = """
            SELECT
                p.*,
                ups.can_write,
                v.version AS current_fault_type_version_number,
                v.description AS current_fault_type_version_description,
                v.is_published AS current_fault_type_is_published,
                v.published_at AS current_fault_type_published_at
            FROM user_project_scopes ups
            JOIN projects p
              ON p.id = ups.project_id
            LEFT JOIN project_fault_type_versions v
              ON v.id = p.fault_type_version_id
            WHERE ups.user_id = ?
        """
    else:
        query = """
            SELECT p.*, ups.can_write
            FROM user_project_scopes ups
            JOIN projects p
              ON p.id = ups.project_id
            WHERE ups.user_id = ?
        """

    params = [user_id]
    if not include_inactive:
        query += " AND p.is_active = 1"
    query += " ORDER BY p.sort_order, p.id"

    projects = [_serialize_project_row(row) for row in db.execute(query, params).fetchall()]
    for project in projects:
        project["can_write"] = bool(project.get("can_write"))
    return projects


def get_default_project_code(projects: list[dict[str, Any]]) -> str | None:
    if not projects:
        return None
    first = sorted(
        projects,
        key=lambda p: (p.get("sort_order", 0), p.get("id", 0), p.get("code", "")),
    )[0]
    return first.get("code")


def get_project_by_code(db, code: str, *, include_inactive: bool = False) -> dict[str, Any] | None:
    projects = get_projects(db, include_inactive=include_inactive)
    for project in projects:
        if project["code"] == code:
            return project
    return None


def can_user_access_project(db, *, user_id: int | None, role: str | None, project_code: str) -> bool:
    if not projects_enabled(db):
        return project_code == "unified"
    if role == "admin":
        return get_project_by_code(db, project_code, include_inactive=True) is not None
    projects = get_visible_projects(
        db,
        user_id=user_id,
        role=role,
        include_inactive=True,
    )
    return any(project["code"] == project_code for project in projects)


def can_user_write_project(db, *, user_id: int | None, role: str | None, project_code: str) -> bool:
    if not projects_enabled(db):
        return role in ("admin", "operator") and project_code == "unified"
    if role == "admin":
        return get_project_by_code(db, project_code, include_inactive=True) is not None
    projects = get_visible_projects(
        db,
        user_id=user_id,
        role=role,
        include_inactive=True,
    )
    return any(project["code"] == project_code and project.get("can_write") for project in projects)


def get_user_project_scope_rows(db, user_id: int) -> list[dict[str, Any]]:
    if not project_scopes_enabled(db):
        return []
    rows = db.execute(
        """
        SELECT project_id, can_write
        FROM user_project_scopes
        WHERE user_id = ?
        ORDER BY project_id
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]
