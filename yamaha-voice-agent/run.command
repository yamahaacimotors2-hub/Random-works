#!/usr/bin/env bash
# Double-click on Mac (or run: bash run.command). Sets everything up, then starts the agent.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python) || { echo "Python পাওয়া যায়নি। python.org থেকে install করুন।"; read -r -p "Enter চাপুন…"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo ">>> .env file খুলছে। GEMINI_API_KEY= এর পরে আপনার key paste করে Save করুন, তারপর এই window এ ফিরে Enter চাপুন।"
  (open -t .env 2>/dev/null || xdg-open .env 2>/dev/null || nano .env)
  read -r -p "Save করার পর Enter চাপুন…"
fi
(sleep 2; open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null) &
.venv/bin/uvicorn server:app --port 8000
