# Yamaha BD সহায়ক – voice agent prototype (web widget)

Unofficial prototype. Not affiliated with or approved by Yamaha. Pitch to Yamaha only after internal review.

## Free-tier stack
- STT: browser Web Speech API (`bn-BD`, Chrome)
- LLM: Gemini free tier (`GEMINI_API_KEY`); without a key it runs in demo mode
- TTS: browser `speechSynthesis` (needs a Bangla voice on the device)
- One click start: Windows `run.bat`, Mac `run.command` (first run asks you to paste the key in `.env`)
- Tools: lead capture and human handoff, written to `leads.jsonl`

## Run
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    cp .env.example .env   # add GEMINI_API_KEY
    set -a; . ./.env; set +a
    .venv/bin/uvicorn server:app --port 8000
Open http://localhost:8000 in Chrome. Test: `.venv/bin/pytest`.

## Knowledge
Edit `knowledge/yamaha_bd.md` with verified facts only. The bot refuses to guess prices or specs.

## Quality upgrade path (low cost)
1. Set `AZURE_SPEECH_KEY` to switch TTS to the Azure Bangla neural voice (`/api/tts`).
2. Replace browser STT with a server STT (Google/Azure) if accuracy is poor.
3. Later: Messenger voice-note webhook, then SIP phone number.
