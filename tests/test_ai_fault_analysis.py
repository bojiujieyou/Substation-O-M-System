import json
from urllib import error

import pytest

from ai_fault_analysis import (
    DailyFaultSummaryAIService,
    NvidiaAISettings,
    probe_nvidia_health,
)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def _reset_ai_env(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("FAULT_AI_ENABLED", "true")
    monkeypatch.delenv("NVIDIA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    monkeypatch.delenv("FAULT_AI_TIMEOUT_SECONDS", raising=False)


def test_probe_nvidia_health_checks_chat_completions_and_records_endpoint(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "timeout": timeout,
                "connection": req.headers.get("Connection"),
            }
        )
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "OK",
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("ai_fault_analysis.request.urlopen", fake_urlopen)

    status = probe_nvidia_health()

    assert status["status"] == "ready"
    assert status["last_operation"] == "health_probe"
    assert status["last_method"] == "POST"
    assert status["last_endpoint"] == "/chat/completions"
    assert calls == [
        {
            "url": "https://integrate.api.nvidia.com/v1/chat/completions",
            "method": "POST",
            "timeout": 12.0,
            "connection": "close",
        }
    ]


def test_analyze_entry_retries_transient_transport_errors(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "timeout": timeout,
                "connection": req.headers.get("Connection"),
            }
        )
        if len(calls) == 1:
            raise error.URLError("SSL EOF")
        return _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "station_name": "220kV睦田变",
                                    "normalized_station_name": "220kV睦田变",
                                    "camera_location_text": "2#主变西南角18枪机",
                                    "fault_type": "摄像头离线",
                                    "confidence": 0.9,
                                    "reason": "描述里有明确机位",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("ai_fault_analysis.request.urlopen", fake_urlopen)

    service = DailyFaultSummaryAIService(
        NvidiaAISettings(
            enabled=True,
            api_key="test-key",
            base_url="https://integrate.api.nvidia.com/v1",
            model="nvidia/llama-3.1-nemotron-nano-8b-v1",
            timeout_seconds=20.0,
        )
    )
    result = service.analyze_entry(
        project_code="unified",
        title="日报",
        source_date="2026-04-07",
        section="测试",
        station_name="220kV睦田变",
        problem_description="220kV睦田变 2#主变西南角18枪机离线",
    )

    assert result["station_name"] == "220kV睦田变"
    assert result["camera_location_text"] == "2#主变西南角18枪机"
    assert result["fault_type"] == "摄像头离线"
    assert len(calls) == 2
    assert all(call["url"].endswith("/chat/completions") for call in calls)
    assert all(call["method"] == "POST" for call in calls)
    assert all(call["timeout"] == 20.0 for call in calls)
    assert all(call["connection"] == "close" for call in calls)
