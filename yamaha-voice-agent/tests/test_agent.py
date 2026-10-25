import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import agent
import server


@pytest.fixture(autouse=True)
def tmp_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "LEADS_FILE", tmp_path / "leads.jsonl")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path / "leads.jsonl"


def test_parse_model_json_with_fences():
    out = agent.parse_model_json('```json\n{"reply":"হ্যালো","action":"none"}\n```')
    assert out["reply"] == "হ্যালো" and out["lead"] == {}


def test_parse_model_json_plain_text_fallback():
    assert agent.parse_model_json("শুধু টেক্সট")["reply"] == "শুধু টেক্সট"


def test_offline_chat_discloses_ai():
    c = TestClient(server.app)
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "হাই"}]})
    assert r.status_code == 200 and "AI" in r.json()["reply"]


def test_chat_rejects_assistant_last():
    c = TestClient(server.app)
    r = c.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_lead_saved(monkeypatch, tmp_leads):
    async def fake(messages, knowledge):
        return {"reply": "ধন্যবাদ", "action": "capture_lead",
                "lead": {"name": "রহিম", "phone": "017", "interest": "test ride", "area": "ঢাকা"}}
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(agent, "call_gemini", fake)
    await agent.respond([{"role": "user", "content": "test ride চাই"}])
    assert json.loads(tmp_leads.read_text(encoding="utf-8"))["phone"] == "017"


@pytest.mark.anyio
async def test_llm_failure_hands_off(monkeypatch, tmp_leads):
    async def boom(messages, knowledge):
        raise RuntimeError
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(agent, "call_gemini", boom)
    out = await agent.respond([{"role": "user", "content": "দাম?"}])
    assert out["action"] == "handoff" and tmp_leads.exists()


@pytest.fixture
def anyio_backend():
    return "asyncio"
