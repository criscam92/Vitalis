"""Endpoints REST del motor de simulación (Fase 2)."""

from __future__ import annotations

from typing import Any, Dict, Iterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from ..core.audit import AuditStore, RunRecorder
from ..core.engine import SimConfig, SimEngine
from ..schemas import ParamsPatch, ResetRequest, StartRequest, StepRequest

router = APIRouter(prefix="/api")

def _engine(request: Request) -> SimEngine:
    engine = request.app.state.engine
    if engine is None:
        raise HTTPException(status_code=400, detail="La simulación no ha sido iniciada")
    return engine

def _audit(request: Request) -> AuditStore:
    store = getattr(request.app.state, "audit", None)
    if store is None:
        raise HTTPException(status_code=503, detail="La auditoría no está disponible")
    return store


def _attach_audit(request: Request, engine: SimEngine) -> None:
    """Abre una corrida nueva en la auditoría y la liga al motor."""
    store = _audit(request)
    store.flush()  # asegura que lo previo ya está en disco antes de abrir corrida
    run_id = store.new_run(engine.config.seed, engine.config.public_dict())
    engine.audit = RunRecorder(store, run_id)
    request.app.state.audit_run = run_id


def _resolve_run(request: Request, run: Optional[int]) -> Optional[int]:
    """Determina la corrida de auditoría a consultar (parámetro > activa > última)."""
    if run is not None:
        return int(run)
    current = getattr(request.app.state, "audit_run", None)
    if current is not None:
        return int(current)
    return _audit(request).latest_run_id()


def state_payload(request: Request) -> Dict[str, Any]:
    """Snapshot completo + fase actual (idle/running/paused)."""
    engine = _engine(request)
    data = engine.snapshot()
    data["phase"] = request.app.state.phase
    data["run_id"] = engine.run_id
    return data


async def broadcast(app) -> None:
    engine = app.state.engine
    # Sin clientes conectados no se calcula el snapshot (con colas grandes es
    # lo más caro del tick). Se mantiene en el event loop: muta las
    # estadísticas del motor, que también avanza en este mismo hilo.
    if engine is None or not app.state.manager.connections:
        return
    data = engine.snapshot()
    data["phase"] = app.state.phase
    data["run_id"] = engine.run_id
    await app.state.manager.broadcast_state(data)


@router.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "vitalis"}


@router.post("/sim/start")
async def start_simulation(req: StartRequest, request: Request) -> Dict[str, Any]:
    """(Re)inicia la corrida con la configuración dada (semilla incluida)."""
    try:
        config = SimConfig(**req.model_dump())
        engine = SimEngine(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.engine = engine
    _attach_audit(request, engine)
    request.app.state.phase = "running"
    await broadcast(request.app)
    return {"ok": True, "state": state_payload(request)}


@router.post("/sim/pause")
async def pause_simulation(request: Request) -> Dict[str, Any]:
    _engine(request)
    request.app.state.phase = "paused"
    await broadcast(request.app)
    return {"ok": True, "phase": "paused"}


@router.post("/sim/resume")
async def resume_simulation(request: Request) -> Dict[str, Any]:
    _engine(request)
    request.app.state.phase = "running"
    await broadcast(request.app)
    return {"ok": True, "phase": "running"}


@router.post("/sim/reset")
async def reset_simulation(req: ResetRequest, request: Request) -> Dict[str, Any]:
    """Reinicia con (opcionalmente) una nueva semilla: la defensa en caliente."""
    engine = _engine(request)
    engine.reset(seed=req.seed)
    _attach_audit(request, engine)
    request.app.state.phase = "running"
    await broadcast(request.app)
    return {"ok": True, "state": state_payload(request)}


@router.post("/sim/step")
async def step_simulation(req: StepRequest, request: Request) -> Dict[str, Any]:
    """Avanza el reloj manualmente (funciona también en pausa)."""
    engine = _engine(request)
    engine.advance(engine.now + req.minutes)
    await broadcast(request.app)
    return {"ok": True, "state": state_payload(request)}


@router.patch("/params")
async def patch_params(req: ParamsPatch, request: Request) -> Dict[str, Any]:
    """Cambia parámetros físicos EN CALIENTE sin reiniciar la corrida."""
    engine = _engine(request)
    try:
        applied = engine.set_params(**req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast(request.app)
    return {"ok": True, "applied": applied, "state": state_payload(request)}


@router.get("/state")
async def get_state(request: Request) -> Dict[str, Any]:
    return state_payload(request)


@router.get("/stats")
async def get_stats(request: Request) -> Dict[str, Any]:
    return state_payload(request)["stats"]


@router.get("/dataset")
async def get_dataset(
    request: Request, run: Optional[int] = Query(None, description="Corrida de auditoría a exportar")
) -> StreamingResponse:
    """CSV de la corrida (auditoría estadística de entrada/salida, Rol C)."""
    _engine(request)
    store = _audit(request)
    store.flush()
    run_id = _resolve_run(request, run)
    if run_id is None:
        raise HTTPException(status_code=400, detail="No hay corrida de auditoría registrada")

    def _rows() -> Iterator[str]:
        yield from store.iter_csv(run_id)

    return StreamingResponse(
        _rows(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=vitalis_llamadas.csv"},
    )

@router.get("/audit/poisson")
async def audit_poisson(
    request: Request,
    delta: float = Query(10.0, gt=0, description="Ancho de ventana en minutos"),
    run: Optional[int] = Query(None, description="Corrida de auditoría a analizar"),
) -> Dict[str, Any]:
    """Histograma agregado para el χ² de Poisson, calculado en el backend.

    Devuelve el nº de ventanas con k llamadas (más total, nWin y horizonte) sin
    traer el dataset completo al navegador: la respuesta es pequeña aunque la
    corrida tenga millones de llamadas.
    """
    store = _audit(request)
    store.flush()
    run_id = _resolve_run(request, run)
    if run_id is None:
        raise HTTPException(status_code=400, detail="No hay corrida de auditoría registrada")

    engine = getattr(request.app.state, "engine", None)
    now = float(engine.now) if engine is not None else 0.0
    result = await run_in_threadpool(store.poisson_histogram, run_id, float(delta), now)
    if result is None:
        raise HTTPException(status_code=400, detail="El dataset está vacío")
    result["run"] = run_id
    return result
