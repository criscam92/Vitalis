"""
FASE 2 — Estadística de entrada/salida (Rol C: Ingeniería de Datos).

Acumula:
  * áreas bajo la curva de la cola y de la ocupación de la flota
    (promedios CONTINUOS, no promedios de muestras discretas),
  * contadores de eventos discretos por nivel de gravedad,
  * histórico de tiempos de respuesta para la gráfica en vivo.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .entities import SEVERITY_NAMES, Call


def _mean(total: float, count: int) -> float:
    return round(total / count, 2) if count else 0.0


class SimStats:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last_t = 0.0
        self.queue_area = 0.0  # ∫ cola(t) dt
        self.busy_area = 0.0  # ∫ ambulancias_ocupadas(t) dt
        self.max_queue = 0
        self.response_history: List[Dict[str, Any]] = []
        self.sev = [
            {
                "arrived": 0,
                "assigned": 0,
                "picked": 0,
                "done": 0,
                "sum_wait": 0.0,
                "sum_response": 0.0,
                "sum_total": 0.0,
            }
            for _ in SEVERITY_NAMES
        ]

    # ---------------------------------------------- acumuladores continuos ----
    def advance_to(self, t: float, queue_len: int, busy_count: int) -> None:
        """Integra los promedios continuos hasta el instante t."""
        if t <= self._last_t:
            return
        dt = t - self._last_t
        self.queue_area += queue_len * dt
        self.busy_area += busy_count * dt
        self._last_t = t

    def observe_queue(self, queue_len: int) -> None:
        if queue_len > self.max_queue:
            self.max_queue = queue_len

    # ------------------------------------------------ eventos discretos -------
    def on_arrival(self, call: Call) -> None:
        self.sev[call.severity]["arrived"] += 1

    def on_assign(self, call: Call) -> None:
        d = self.sev[call.severity]
        d["assigned"] += 1
        d["sum_wait"] += call.t_assigned - call.t_arrival

    def on_pick(self, call: Call) -> None:
        d = self.sev[call.severity]
        d["picked"] += 1
        d["sum_response"] += call.t_picked - call.t_arrival

    def on_done(self, call: Call) -> None:
        d = self.sev[call.severity]
        d["done"] += 1
        d["sum_total"] += call.total_min
        self.response_history.append(
            {
                "id": call.id,
                "t": round(call.t_done, 2),
                "minutes": round(call.response_min, 2),
                "total": round(call.total_min, 2),
                "severity": call.severity,
            }
        )
        if len(self.response_history) > 120:
            self.response_history.pop(0)

    # ------------------------------------------------------------ resumen -----
    def summary(
        self,
        now: float,
        queue_len: int,
        busy_count: int,
        num_ambulances: int,
    ) -> Dict[str, Any]:
        horizon = max(now, 1e-9)
        totals = {
            k: sum(d[k] for d in self.sev)
            for k in ("arrived", "assigned", "picked", "done", "sum_wait", "sum_response", "sum_total")
        }
        by_severity = []
        for i, name in enumerate(SEVERITY_NAMES):
            d = self.sev[i]
            by_severity.append(
                {
                    "severity": i,
                    "name": name,
                    "arrived": d["arrived"],
                    "served": d["done"],
                    "waiting": d["arrived"] - d["assigned"],
                    "active": d["assigned"] - d["done"],
                    "avg_wait_min": _mean(d["sum_wait"], d["assigned"]),
                    "avg_response_min": _mean(d["sum_response"], d["picked"]),
                }
            )
        utilization = (self.busy_area / (num_ambulances * horizon) * 100.0) if num_ambulances else 0.0
        return {
            "calls_total": totals["arrived"],
            "calls_served": totals["done"],
            "calls_waiting": queue_len,
            "calls_active": totals["assigned"] - totals["done"],
            "avg_wait_min": _mean(totals["sum_wait"], totals["assigned"]),
            "avg_response_min": _mean(totals["sum_response"], totals["picked"]),
            "avg_total_min": _mean(totals["sum_total"], totals["done"]),
            "avg_queue_min": round(self.queue_area / horizon, 2),
            "max_queue": self.max_queue,
            "utilization_pct": round(utilization, 1),
            "busy_ambulances": busy_count,
            "idle_ambulances": max(num_ambulances - busy_count, 0),
            "by_severity": by_severity,
            "response_history": list(self.response_history),
        }
