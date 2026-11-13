"""Streaming en tiempo real por WebSocket (alimenta la animación del mapa)."""

from __future__ import annotations

from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    """Administra los clientes conectados al canal de simulación."""

    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast_state(self, state: Dict[str, Any]) -> None:
        if not self.connections:
            return
        payload = {"type": "state", "data": state}
        dead = []
        for ws in list(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


@router.websocket("/ws/sim")
async def websocket_sim(websocket: WebSocket) -> None:
    """Canal unidireccional: el servidor emite el snapshot ~10 veces por segundo."""
    manager: ConnectionManager = websocket.app.state.manager
    await manager.connect(websocket)
    engine = getattr(websocket.app.state, "engine", None)
    try:
        if engine is not None:
            data = engine.snapshot()
            data["phase"] = getattr(websocket.app.state, "phase", "idle")
            data["run_id"] = engine.run_id
            await websocket.send_json({"type": "state", "data": data})
        while True:
            await websocket.receive_text()  # el cliente solo mantiene vivo el canal
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
