"""Yamaha BD voice agent brain: knowledge base + LLM (Gemini free tier) + tools."""
import json
import os
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
LEADS_FILE = ROOT / "leads.jsonl"


def load_env_file() -> None:
    """Read KEY=VALUE lines from .env (does not override variables already set)."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                os.environ.setdefault(k.strip(), v)


load_env_file()

SYSTEM_PROMPT = """তুমি "Yamaha BD সহায়ক", Yamaha মোটরসাইকেল সম্পর্কে গ্রাহকদের সাহায্যকারী একটি AI ভয়েস সহকারী (এটি একটি প্রোটোটাইপ, Yamaha-র অফিশিয়াল প্রতিনিধি নয়)।

নিয়ম:
1. প্রথম উত্তরে নিজেকে AI সহকারী বলে পরিচয় দাও।
2. সহজ, ছোট (১-৩ বাক্য) চলিত বাংলায় উত্তর দাও, কারণ উত্তর কণ্ঠে পড়ে শোনানো হবে। ইংরেজি বললে ইংরেজিতে উত্তর দাও।
3. শুধু নিচের KNOWLEDGE থেকে মডেল, দাম, spec, showroom, service বলো। সেখানে না থাকলে অনুমান করবে না। বলো "এই তথ্যটি আমার কাছে নেই" এবং হিউম্যান প্রতিনিধির কাছে পাঠানোর প্রস্তাব দাও।
4. গ্রাহক test ride, service booking বা কিনতে আগ্রহী হলে নাম, ফোন, মডেল/বিষয় ও এলাকা জিজ্ঞেস করো (একটি একটি করে)। সব পেলে action = "capture_lead"।
5. অভিযোগ, জরুরি বিষয় বা মানুষের সাথে কথা চাইলে action = "handoff"।

আউটপুট শুধু JSON: {"reply": "...", "action": "none" | "capture_lead" | "handoff", "lead": {"name": "", "phone": "", "interest": "", "area": ""}}

KNOWLEDGE:
{knowledge}
"""


def load_knowledge() -> str:
    parts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "knowledge").glob("*.md"))]
    return "\n\n".join(parts)


def save_lead(kind: str, lead: dict) -> None:
    rec = {"ts": int(time.time()), "kind": kind, **lead}
    with LEADS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse_model_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except json.JSONDecodeError:
        return {"reply": text, "action": "none", "lead": {}}
    data.setdefault("reply", "")
    data.setdefault("action", "none")
    data.setdefault("lead", {})
    return data


async def call_gemini(messages: list[dict], knowledge: str) -> dict:
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT.replace("{knowledge}", knowledge)}]},
        "contents": [
            {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
            for m in messages
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, params={"key": key}, json=body)
        r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return parse_model_json(text)


def offline_reply(messages: list[dict]) -> dict:
    """No API key: keep the widget testable end to end."""
    first = len([m for m in messages if m["role"] == "user"]) == 1
    prefix = "আমি Yamaha BD সহায়ক, একটি AI সহকারী। " if first else ""
    return {
        "reply": prefix + "এখন ডেমো মোড চালু আছে, GEMINI_API_KEY দিলে আমি আসল উত্তর দিতে পারব।",
        "action": "none",
        "lead": {},
    }


async def respond(messages: list[dict]) -> dict:
    messages = messages[-20:]
    if os.environ.get("GEMINI_API_KEY"):
        try:
            result = await call_gemini(messages, load_knowledge())
        except Exception:
            result = {
                "reply": "দুঃখিত, একটু সমস্যা হচ্ছে। আপনাকে হিউম্যান প্রতিনিধির কাছে পাঠাচ্ছি।",
                "action": "handoff",
                "lead": {},
            }
    else:
        result = offline_reply(messages)
    lead = result.get("lead") or {}
    if result["action"] == "capture_lead" and lead.get("phone"):
        save_lead("lead", lead)
    elif result["action"] == "handoff":
        save_lead("handoff", {**lead, "last_user_msg": messages[-1]["content"] if messages else ""})
    return result
