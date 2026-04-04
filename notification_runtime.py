from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


LOGGER = logging.getLogger("station_monitor.notifications")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def table_exists(db, table_name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def feature_available(db) -> bool:
    required_tables = {
        "projects",
        "project_notification_policies",
        "project_notification_configs",
        "fault_reports",
    }
    return all(table_exists(db, table_name) for table_name in required_tables)


def ensure_dispatch_log_table(db) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_dispatch_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fault_id INTEGER NOT NULL,
            project_id INTEGER,
            policy_id INTEGER,
            config_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            channel TEXT NOT NULL,
            target_value TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            payload_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fault_id, config_id, event_type)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notification_dispatch_logs_fault_event
        ON notification_dispatch_logs(fault_id, event_type, created_at)
        """
    )


def _parse_json_text(value):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value) -> bool:
    return bool(int(value)) if isinstance(value, (int, bool)) else bool(value)


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_quiet_hours(now_utc: datetime, quiet_hours: dict | None) -> bool:
    if not quiet_hours:
        return False
    start = str(quiet_hours.get("start") or "").strip()
    end = str(quiet_hours.get("end") or "").strip()
    if not start or not end:
        return False

    try:
        start_hour, start_minute = [int(part) for part in start.split(":", 1)]
        end_hour, end_minute = [int(part) for part in end.split(":", 1)]
    except (ValueError, TypeError):
        return False

    now_local = now_utc.astimezone(LOCAL_TZ)
    now_minutes = now_local.hour * 60 + now_local.minute
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute

    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= now_minutes < end_minutes
    return now_minutes >= start_minutes or now_minutes < end_minutes


def _load_fault(db, fault_id: int):
    columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(fault_reports)").fetchall()
    }
    select_fields = [
        "f.id",
        "f.project_id",
        "f.station_id",
        "f.status",
        "f.created_at",
        "s.name AS station_name",
    ]
    optional_fields = {
        "closed_at": "f.closed_at",
        "handling_started_at": "f.handling_started_at",
        "fault_type": "f.fault_type",
        "fault_type_label_snapshot": "f.fault_type_label_snapshot",
        "description": "f.description",
    }
    for field_name, expression in optional_fields.items():
        if field_name in columns:
            select_fields.append(expression)
        else:
            select_fields.append(f"NULL AS {field_name}")

    return db.execute(
        f"""
        SELECT
            {", ".join(select_fields)}
        FROM fault_reports f
        LEFT JOIN stations s ON s.id = f.station_id
        WHERE f.id = ?
        """,
        (fault_id,),
    ).fetchone()


def _load_policy_bundle(db, project_id: int):
    project = db.execute(
        """
        SELECT id, code, name, short_name, is_active
        FROM projects
        WHERE id = ?
        """,
        (project_id,),
    ).fetchone()
    if not project:
        return None, None, []

    policy = db.execute(
        """
        SELECT *
        FROM project_notification_policies
        WHERE project_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if not policy:
        return project, None, []

    configs = db.execute(
        """
        SELECT *
        FROM project_notification_configs
        WHERE policy_id = ?
          AND is_active = 1
        ORDER BY id
        """,
        (policy["id"] if hasattr(policy, "keys") else policy[0],),
    ).fetchall()
    return project, policy, configs


def _resolve_event_configs(policy, configs, event_type: str):
    if not policy:
        return [], "policy_missing"

    if not _coerce_bool(policy["is_active"]):
        return [], "policy_inactive"

    if event_type == "fault_created" and not _coerce_bool(policy["notify_on_create"]):
        return [], "notify_on_create_disabled"
    if event_type == "fault_closed" and not _coerce_bool(policy["notify_on_close"]):
        return [], "notify_on_close_disabled"
    if event_type == "fault_escalated":
        if not policy["escalate_after_minutes"]:
            return [], "escalation_disabled"
        target_id = policy["escalation_target_config_id"]
        if target_id:
            target = next((item for item in configs if item["id"] == target_id), None)
            return ([target] if target else []), ("escalation_target_missing" if not target else None)

    selected = [item for item in configs if item["event_type"] == event_type]
    return selected, ("no_active_configs" if not selected else None)


def _build_dispatch_payload(fault, project, policy, config, event_type: str, now_utc: datetime):
    return {
        "fault_id": fault["id"],
        "project_id": project["id"],
        "project_code": project["code"],
        "project_name": project["name"],
        "station_id": fault["station_id"],
        "station_name": fault["station_name"],
        "event_type": event_type,
        "fault_status": fault["status"],
        "fault_type": fault["fault_type_label_snapshot"] or fault["fault_type"],
        "description": fault["description"],
        "channel": config["channel"],
        "target_value": config["target_value"],
        "policy_id": policy["id"],
        "config_id": config["id"],
        "generated_at_utc": now_utc.isoformat(),
    }


def dispatch_notification_event(
    db,
    fault_id: int,
    event_type: str,
    *,
    now: datetime | None = None,
    logger=None,
    auto_commit: bool = True,
):
    logger = logger or LOGGER
    summary = {
        "fault_id": fault_id,
        "event_type": event_type,
        "dispatched": [],
        "suppressed_reason": None,
    }

    if not feature_available(db):
        summary["suppressed_reason"] = "feature_unavailable"
        return summary
    ensure_dispatch_log_table(db)

    fault = _load_fault(db, fault_id)
    if not fault:
        summary["suppressed_reason"] = "fault_not_found"
        return summary
    if not fault["project_id"]:
        summary["suppressed_reason"] = "fault_has_no_project"
        return summary

    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    project, policy, configs = _load_policy_bundle(db, fault["project_id"])
    if not project:
        summary["suppressed_reason"] = "project_not_found"
        return summary
    if not _coerce_bool(project["is_active"]):
        summary["suppressed_reason"] = "project_inactive"
        return summary

    quiet_hours = _parse_json_text(policy["quiet_hours_json"]) if policy else None
    if _is_quiet_hours(now_utc, quiet_hours):
        summary["suppressed_reason"] = "quiet_hours"
        return summary

    configs_to_use, suppressed_reason = _resolve_event_configs(policy, configs, event_type)
    if suppressed_reason:
        summary["suppressed_reason"] = suppressed_reason
        return summary

    ensure_dispatch_log_table(db)
    for config in configs_to_use:
        payload = _build_dispatch_payload(fault, project, policy, config, event_type, now_utc)
        existing = db.execute(
            """
            SELECT id
            FROM notification_dispatch_logs
            WHERE fault_id = ? AND config_id = ? AND event_type = ?
            """,
            (fault_id, config["id"], event_type),
        ).fetchone()
        if existing:
            continue

        db.execute(
            """
            INSERT INTO notification_dispatch_logs (
                fault_id, project_id, policy_id, config_id, event_type,
                channel, target_value, status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)
            """,
            (
                fault_id,
                project["id"],
                policy["id"],
                config["id"],
                event_type,
                config["channel"],
                config["target_value"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        summary["dispatched"].append(
            {
                "config_id": config["id"],
                "channel": config["channel"],
                "target_value": config["target_value"],
            }
        )

    if summary["dispatched"]:
        if auto_commit:
            db.commit()
        logger.info(
            "Notification planned: fault=%s event=%s targets=%s",
            fault_id,
            event_type,
            json.dumps(summary["dispatched"], ensure_ascii=False),
        )
    elif summary["suppressed_reason"] is None:
        summary["suppressed_reason"] = "already_dispatched_or_no_match"
    return summary


def find_escalation_candidates(db, *, now: datetime | None = None):
    if not feature_available(db):
        return []

    ensure_dispatch_log_table(db)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    rows = db.execute(
        """
        SELECT
            f.id AS fault_id,
            f.project_id,
            f.status,
            COALESCE(f.handling_started_at, f.created_at) AS reference_time,
            p.code AS project_code,
            p.is_active AS project_is_active,
            np.id AS policy_id,
            np.is_active AS policy_is_active,
            np.escalate_after_minutes,
            np.quiet_hours_json
        FROM fault_reports f
        JOIN projects p ON p.id = f.project_id
        JOIN project_notification_policies np ON np.project_id = p.id
        WHERE f.project_id IS NOT NULL
          AND f.status IN ('open', 'handling')
          AND np.escalate_after_minutes IS NOT NULL
          AND np.escalate_after_minutes > 0
        """
    ).fetchall()

    candidates = []
    for row in rows:
        if not _coerce_bool(row["project_is_active"]):
            continue
        if not _coerce_bool(row["policy_is_active"]):
            continue
        reference_time = _parse_datetime(row["reference_time"])
        if reference_time is None:
            continue
        age_minutes = (now_utc - reference_time.astimezone(timezone.utc)).total_seconds() / 60
        if age_minutes < row["escalate_after_minutes"]:
            continue
        already_sent = db.execute(
            """
            SELECT 1
            FROM notification_dispatch_logs
            WHERE fault_id = ? AND event_type = 'fault_escalated'
            LIMIT 1
            """,
            (row["fault_id"],),
        ).fetchone()
        if already_sent:
            continue
        candidates.append(
            {
                "fault_id": row["fault_id"],
                "project_id": row["project_id"],
                "project_code": row["project_code"],
                "reference_time": row["reference_time"],
                "age_minutes": int(age_minutes),
                "quiet_hours": _parse_json_text(row["quiet_hours_json"]),
                "project_is_active": _coerce_bool(row["project_is_active"]),
                "policy_is_active": _coerce_bool(row["policy_is_active"]),
            }
        )
    return candidates


def dispatch_pending_escalations(db, *, now: datetime | None = None, logger=None):
    logger = logger or LOGGER
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    report = {"candidates": 0, "dispatched": 0, "suppressed": []}
    for candidate in find_escalation_candidates(db, now=now_utc):
        report["candidates"] += 1
        summary = dispatch_notification_event(
            db,
            candidate["fault_id"],
            "fault_escalated",
            now=now_utc,
            logger=logger,
            auto_commit=False,
        )
        if summary["dispatched"]:
            report["dispatched"] += len(summary["dispatched"])
        else:
            report["suppressed"].append(
                {
                    "fault_id": candidate["fault_id"],
                    "reason": summary["suppressed_reason"],
                }
            )
    return report
