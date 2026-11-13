from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router as api_router
from .api.routes import broadcast
from .api.ws import ConnectionManager, router as ws_router
from .core.audit import AuditStore

TICK_SECONDS = 0.1  # 10 actualizaciones por segundo para la animación

async def _simulation_loop(app: FastAPI) -> None:
    """Bucle en tiempo real: avanza el reloj y emite el estado por WebSocket."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        engine = app.state.engine
        if engine is None or app.state.phase != "running":
            continue
        delta_min = TICK_SECONDS * engine.config.clock_speed
        engine.advance(engine.now + delta_min)
        await broadcast(app)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = None
    app.state.phase = "idle"  # idle | running | paused
    app.state.manager = ConnectionManager()
    app.state.audit = AuditStore()
    app.state.audit_run = None
    loop_task = asyncio.create_task(_simulation_loop(app))
    yield
    loop_task.cancel()
    app.state.audit.close()


app = FastAPI(
    title="VITALIS – Sistema Logístico y de Despacho de Emergencias Médicas",
    description=(
        "Simulación híbrida: llamadas Poisson (eventos discretos) + tiempo de "
        "viaje continuo por distancia euclídea. Semilla y parámetros alterables en vivo."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ws_router)

# Frontend (GUI con mapa Leaflet) servido por el mismo FastAPI ---------------
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.middleware("http")
async def no_cache_frontend(request: Request, call_next):
    """Fuerza la revalidación de HTML/JS/CSS para no servir una GUI antigua."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response
