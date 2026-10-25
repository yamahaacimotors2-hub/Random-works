@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Python paowa jayni. python.org theke install korun, "Add to PATH" tick diye. & pause & exit /b 1)
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo .env file khulche. GEMINI_API_KEY= er pore apnar key paste kore Save korun.
  notepad .env
)
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"
.venv\Scripts\uvicorn server:app --port 8000
pause
