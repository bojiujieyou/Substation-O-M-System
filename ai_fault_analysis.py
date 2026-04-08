import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import error, request


logger = logging.getLogger(__name__)

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = os.environ.get(
    "NVIDIA_MODEL",
    "nvidia/llama-3.1-nemotron-nano-8b-v1",
)

AI_RUNTIME_STATE = {
    "provider": "nvidia",
    "status": "not_configured",
    "message": "NVIDIA_API_KEY not configured; falling back to rules",
    "last_error": None,
    "last_checked_at": None,
    "last_operation": None,
    "last_endpoint": None,
    "last_method": None,
}


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = set()
    for row in rows:
        if hasattr(row, "keys"):
            names.add(row["name"])
        else:
            names.add(row[1])
    return names


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_columns(conn, table_name: str, columns: dict[str, str]) -> None:
    if not _table_exists(conn, table_name):
        return
    existing = _table_columns(conn, table_name)
    for column_name, column_sql in columns.items():
        if column_name in existing:
            continue
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def ensure_ai_runtime_schema(conn) -> None:
    _ensure_columns(
        conn,
        "fault_reports",
        {
            "camera_location_text": "TEXT",
            "ai_confidence": "REAL",
            "ai_trace_json": "TEXT",
        },
    )
    _ensure_columns(
        conn,
        "fault_import_review_queue",
        {
            "ai_suggestion_json": "TEXT",
            "ai_confidence": "REAL",
            "ai_reason": "TEXT",
        },
    )


def _set_runtime_state(
    *,
    status: str,
    message: str,
    last_error: str | None = None,
    last_operation: str | None = None,
    last_endpoint: str | None = None,
    last_method: str | None = None,
) -> None:
    AI_RUNTIME_STATE["status"] = status
    AI_RUNTIME_STATE["message"] = message
    AI_RUNTIME_STATE["last_error"] = last_error
    AI_RUNTIME_STATE["last_checked_at"] = _now_iso()
    AI_RUNTIME_STATE["last_operation"] = last_operation
    AI_RUNTIME_STATE["last_endpoint"] = last_endpoint
    AI_RUNTIME_STATE["last_method"] = last_method


def get_ai_runtime_status() -> dict[str, Any]:
    settings = load_nvidia_settings()
    enabled = settings.enabled
    status = dict(AI_RUNTIME_STATE)
    status.update(
        {
            "provider": "nvidia",
            "enabled": enabled,
            "configured": bool(settings.api_key),
            "base_url": settings.base_url,
            "model": settings.model,
            "timeout_seconds": settings.timeout_seconds,
        }
    )
    if not settings.api_key:
        status["status"] = "not_configured"
        status["message"] = "未配置 NVIDIA_API_KEY，当前会自动回退到规则导入。"
    elif not _parse_bool(os.environ.get("FAULT_AI_ENABLED"), True):
        status["status"] = "disabled"
        status["message"] = "已通过 FAULT_AI_ENABLED=false 关闭 AI，当前会自动回退到规则导入。"
    elif status.get("status") == "not_configured":
        status["status"] = "ready"
        status["message"] = "AI 已配置，当前可用于辅助导入分析。"
    return status


def normalize_camera_hint(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^[|,:;，；：\s]+", "", text)
    text = re.sub(
        r"^(省公司平台|平台|系统平台)?\s*(离线摄像头|离线球机|离线枪机|离线设备|摄像头离线|球机离线|枪机离线)\s*[|：: ]*",
        "",
        text,
    )
    text = re.sub(r"[（(].*?[）)]", "", text).strip()
    if not text:
        return ""
    match = re.search(
        r"(?P<location>.+?(?:枪机|球机|半球|云台|摄像头|通道\d+|#\d+))(?:(?:离线|掉线|中断|异常|故障|黑屏|模糊).*)?$",
        text,
    )
    if match:
        return match.group("location").strip(" |,，;；：:")
    text = re.sub(r"(离线|掉线|中断|异常|故障|黑屏|模糊).*$", "", text).strip(" |,，;；：:")
    return text


def build_ai_trace(result: dict[str, Any] | None, *, provider: str, model: str, enabled: bool, error_message: str | None = None) -> str | None:
    if not any([result, error_message, enabled]):
        return None
    payload = {
        "provider": provider,
        "model": model,
        "enabled": enabled,
    }
    if result:
        payload["result"] = result
    if error_message:
        payload["error"] = error_message
    return _json_dumps(payload)


def _extract_first_json_block(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _looks_like_echo_payload(parsed: dict[str, Any]) -> bool:
    if not parsed:
        return True
    if parsed.get("task") == "extract_daily_fault_summary":
        return True
    useful_keys = {"camera_location_text", "fault_type", "normalized_station_name", "reason"}
    useful_values = [str(parsed.get(key) or "").strip() for key in useful_keys]
    return not any(useful_values)


def _sanitize_station_name(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if len(text) < 4 and len(fallback) >= len(text):
        return fallback
    return text


def _coerce_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
        return "\n".join(parts)
    return str(content or "")


@dataclass
class NvidiaAISettings:
    enabled: bool
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float


def load_nvidia_settings() -> NvidiaAISettings:
    api_key = (os.environ.get("NVIDIA_API_KEY") or "").strip()
    base_url = (os.environ.get("NVIDIA_BASE_URL") or DEFAULT_NVIDIA_BASE_URL).rstrip("/")
    model = (os.environ.get("NVIDIA_MODEL") or DEFAULT_NVIDIA_MODEL).strip()
    timeout_seconds = float(os.environ.get("FAULT_AI_TIMEOUT_SECONDS", "20"))
    enabled = _parse_bool(os.environ.get("FAULT_AI_ENABLED"), True) and bool(api_key)
    return NvidiaAISettings(
        enabled=enabled,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
    )


def _build_nvidia_request(
    settings: NvidiaAISettings,
    *,
    endpoint: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> request.Request:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.api_key}",
        # Some transient NVIDIA edge/proxy failures are less frequent when we
        # avoid reusing a half-closed TLS connection.
        "Connection": "close",
        "User-Agent": "station-monitor-ai/1.0",
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    return request.Request(
        f"{settings.base_url}{endpoint}",
        data=body,
        headers=headers,
        method=method,
    )


def _invoke_nvidia_json(
    settings: NvidiaAISettings,
    *,
    endpoint: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
    attempts: int = 3,
    operation_name: str = "request",
) -> dict[str, Any]:
    last_exc = None
    for attempt in range(attempts):
        try:
            req = _build_nvidia_request(
                settings,
                endpoint=endpoint,
                method=method,
                payload=payload,
            )
            with request.urlopen(req, timeout=timeout_seconds or settings.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_exc = exc
            logger.warning(
                "NVIDIA %s failed (attempt %s %s %s): %s",
                operation_name,
                attempt + 1,
                method,
                endpoint,
                exc,
            )
            if attempt < attempts - 1:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(str(last_exc) if last_exc else f"{operation_name} failed")


def _build_healthcheck_payload(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Reply with OK only.",
            },
            {
                "role": "user",
                "content": "health check",
            },
        ],
        "temperature": 0,
        "max_tokens": 2,
        "stream": False,
    }


class DailyFaultSummaryAIService:
    provider_name = "nvidia"

    def __init__(self, settings: NvidiaAISettings | None = None):
        self.settings = settings or load_nvidia_settings()

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def analyze_entry(
        self,
        *,
        project_code: str,
        title: str,
        source_date: str,
        section: str,
        station_name: str,
        problem_description: str,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            _set_runtime_state(
                status="fallback",
                message="AI 未启用，当前使用规则导入。",
            )
            return None

        system_prompt = (
            "你是中文电力视频监控运维数据抽取助手，只能输出一个 JSON 对象。"
            "不要输出 Markdown，不要解释，不要重复输入字段。"
            "你要从问题描述中抽取最具体的摄像头位置短语，例如“2#主变西南角#18枪机”“大门东北侧-1#球机”。"
            "camera_location_text 不能填写“省公司平台”“离线摄像头”“故障”这类泛化词，除非原文只有这些且没有更具体位置。"
            "fault_type 只允许从以下值中选择一个：摄像头离线、视频质量异常、网络通信异常、未知。"
            "normalized_station_name 只保留站名主体，不要删除电压等级。"
            "返回字段必须包含 station_name, normalized_station_name, camera_location_text, fault_type, confidence, reason。"
            "confidence 必须是 0 到 1 之间的小数。"
            "无法判断时返回空字符串，但仍必须保持 JSON 合法。"
        )
        user_prompt = _json_dumps(
            {
                "task": "extract_daily_fault_summary",
                "project_code": project_code,
                "title": title,
                "source_date": source_date,
                "section": section,
                "station_name": station_name,
                "problem_description": problem_description,
                "output_example": {
                    "station_name": station_name,
                    "normalized_station_name": station_name,
                    "camera_location_text": "2#主变西南角#18枪机",
                    "fault_type": "摄像头离线",
                    "confidence": 0.92,
                    "reason": "问题描述里出现了具体机位短语",
                },
            }
        )
        parsed = self._chat_json(system_prompt, user_prompt)
        if not parsed:
            return None
        parsed["station_name"] = _sanitize_station_name(parsed.get("station_name") or "", station_name)
        if not parsed.get("normalized_station_name"):
            parsed["normalized_station_name"] = parsed["station_name"]
        return parsed

    def _chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any] | None:
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "top_p": 0.7,
            "max_tokens": 500,
            "stream": False,
        }
        endpoint = "/chat/completions"
        try:
            response_json = _invoke_nvidia_json(
                self.settings,
                endpoint=endpoint,
                method="POST",
                payload=payload,
                attempts=4,
                operation_name="chat completion",
            )
        except RuntimeError as exc:
            _set_runtime_state(
                status="error",
                message="NVIDIA AI 调用失败，当前已自动回退到规则导入。",
                last_error=str(exc),
                last_operation="chat_completion",
                last_endpoint=endpoint,
                last_method="POST",
            )
            return None

        choices = response_json.get("choices") or []
        if not choices:
            _set_runtime_state(
                status="error",
                message="NVIDIA AI 未返回有效候选结果，当前已自动回退到规则导入。",
                last_error="no choices returned",
                last_operation="chat_completion",
                last_endpoint=endpoint,
                last_method="POST",
            )
            return None
        message = choices[0].get("message") or {}
        content = _coerce_message_text(message.get("content"))
        parsed = _extract_first_json_block(content)
        if not parsed or _looks_like_echo_payload(parsed):
            logger.warning("NVIDIA AI response did not contain valid JSON")
            _set_runtime_state(
                status="error",
                message="NVIDIA AI 返回结果不可解析，当前已自动回退到规则导入。",
                last_error=content[:300],
                last_operation="chat_completion",
                last_endpoint=endpoint,
                last_method="POST",
            )
            return None

        confidence = parsed.get("confidence")
        try:
            parsed["confidence"] = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            parsed["confidence"] = None

        for key in [
            "station_name",
            "normalized_station_name",
            "camera_location_text",
            "fault_type",
            "reason",
        ]:
            parsed[key] = str(parsed.get(key) or "").strip()
        if not parsed["normalized_station_name"]:
            parsed["normalized_station_name"] = parsed["station_name"]
        if parsed["fault_type"] not in {"摄像头离线", "视频质量异常", "网络通信异常", "未知", ""}:
            parsed["fault_type"] = "未知"
        if parsed["camera_location_text"] in {"省公司平台", "离线摄像头", "故障"}:
            parsed["camera_location_text"] = ""
        _set_runtime_state(
            status="ready",
            message="AI 已配置，当前可用于辅助导入分析。",
            last_operation="chat_completion",
            last_endpoint=endpoint,
            last_method="POST",
        )
        return parsed


def probe_nvidia_health(timeout_seconds: float = 8.0) -> dict[str, Any]:
    settings = load_nvidia_settings()
    if not settings.api_key:
        _set_runtime_state(
            status="not_configured",
            message="未配置 NVIDIA_API_KEY，当前会自动回退到规则导入。",
            last_operation="health_probe",
        )
        return get_ai_runtime_status()
    if not _parse_bool(os.environ.get("FAULT_AI_ENABLED"), True):
        _set_runtime_state(
            status="disabled",
            message="已通过 FAULT_AI_ENABLED=false 关闭 AI，当前会自动回退到规则导入。",
            last_operation="health_probe",
        )
        return get_ai_runtime_status()
    endpoint = "/chat/completions"
    try:
        response_json = _invoke_nvidia_json(
            settings,
            endpoint=endpoint,
            method="POST",
            payload=_build_healthcheck_payload(settings.model),
            timeout_seconds=max(timeout_seconds, min(settings.timeout_seconds, 12.0)),
            attempts=2,
            operation_name="health probe",
        )
        if response_json.get("choices"):
            _set_runtime_state(
                status="ready",
                message="AI 已配置，生成接口探测通过，可用于辅助导入分析。",
                last_operation="health_probe",
                last_endpoint=endpoint,
                last_method="POST",
            )
            return get_ai_runtime_status()
    except RuntimeError as exc:
        last_error = str(exc)
    else:
        last_error = "health probe returned no choices"
    _set_runtime_state(
        status="error",
        message="NVIDIA AI 生成接口当前不可用，系统会自动回退到规则导入。",
        last_error=last_error,
        last_operation="health_probe",
        last_endpoint=endpoint,
        last_method="POST",
    )
    return get_ai_runtime_status()
