# Cursor prompt: run MedGate on this Windows PC

Paste everything below the line into Cursor Agent (Agent mode, terminal allowed), with this folder open as the workspace.

---

You are running an EXISTING, already-tested project on Windows. Goal: the dashboard at http://127.0.0.1:8000 loads. Do NOT rewrite, refactor, restyle or "improve" any code. Only fix what blocks startup, and change as few lines as possible.

Use tokens wisely: do not read whole files or docs unless a step fails. Do not open README/other .md files. Run commands, read only the error lines, act. Keep replies short.

Facts (already verified, do not re-investigate):
- Backend: FastAPI app `medgate.api:app` in `backend/`. Python 3.10 expected.
- This PC cannot reach pypi.org (DNS fails). All dependencies are bundled as Windows cp310 wheels in `backend/wheels`. Always install with `--no-index --find-links wheels`.
- `backend/.venv` may exist half-built from an earlier failed install. If imports fail, delete it and recreate it.
- 140 tests already pass on Linux; frontend is static files served by the backend (no npm, no build).

Steps (PowerShell, run from the workspace root):
1. `cd backend`
2. Find Python 3.10: try `py -3.10 --version`, then `python --version`. Use whichever reports 3.10.x. If no 3.10 exists, use the available Python 3.10–3.13 and in step 4 use online install `pip install -r requirements.txt` (the wheels only fit 3.10); if online fails too, stop and report.
3. If `.venv\Scripts\python.exe -c "import fastapi, uvicorn, httpx"` fails: `Remove-Item -Recurse -Force .venv`, then `<python310> -m venv .venv`.
4. `.venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt`
5. `.venv\Scripts\python.exe -m pytest -q` — expect `140 passed`. If tests fail, show the first failure only and fix the smallest cause.
6. Start the server in a background terminal that stays open:
   `.venv\Scripts\python.exe -m uvicorn medgate.api:app --host 127.0.0.1 --port 8000`
7. Verify in a second terminal: `curl.exe -s http://127.0.0.1:8000/api/health` must print `{"ok":true,...}`.
8. Open http://127.0.0.1:8000 in the browser. The page should show "MedGate" and a SIMULATOR device. Click Simulator → Run "normal" and confirm a dose reaches COLLECTED.

If something fails:
- Port 8000 busy: `netstat -ano | findstr :8000`, stop that PID, or use `--port 8010` and open that port instead.
- `ModuleNotFoundError: medgate`: you are not in `backend/`.
- Antivirus/firewall prompt: allow Python on private networks.
- Old database error: stop server, delete `backend\medgate.db*`, start again.

Report back in 5 lines max: Python version used, install result, test result, health check output, and whether the dashboard loaded. Leave the server running.
