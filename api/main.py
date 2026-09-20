"""FastAPI app: the Trust Engine API plus a minimal static dashboard.

Exposes the five capabilities the implementation spec's tech-stack
section calls for: ingesting a signal, the approval queue (list +
resolve), rule/tier state, and running a simulation. The dashboard
(frontend/) is a small static HTML/JS page, not a separate Next.js build
— kept lean per the "favor a clean, functional table-based UI over
visual polish" guidance.

Run with: uvicorn api.main:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import approval_queue, rules, signals, simulation

BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BASE_DIR / "frontend"
SIMULATION_OUTPUT_DIR = BASE_DIR / "simulation" / "output"
SIMULATION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Trust Engine API")

app.include_router(rules.router)
app.include_router(signals.router)
app.include_router(approval_queue.router)
app.include_router(simulation.router)

app.mount("/static/app", StaticFiles(directory=FRONTEND_DIR), name="frontend-assets")
app.mount("/static/simulation-output", StaticFiles(directory=SIMULATION_OUTPUT_DIR), name="simulation-output")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
