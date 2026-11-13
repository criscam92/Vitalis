"""Auditoría persistente en disco (Rol C: Ingeniería de Datos).

Guarda las llamadas de la corrida en una base SQLite local para poder recorrer
corridas arbitrariamente largas (hasta agotar el período del PRNG) SIN saturar
la memoria RAM: el motor solo retiene las llamadas activas/en cola y la
auditoría completa vive en disco.

Para no alterar la auditoría respecto al comportamiento original, cada llamada
se registra **al ser creada** (con su `t_llegada` y posición) y la fila se
**actualiza al completarse** (asignación, recogida, fin y tiempos derivados).
Así el dataset incluye también las llamadas en curso o en espera.

De aquí se alimentan las dos piezas de la auditoría del frontend:

  * GET /api/dataset        -> CSV completo de la corrida (en *streaming*).
  * GET /api/audit/poisson  -> histograma agregado para el χ² de Poisson.

Concurrencia: un único escritor (el bucle de simulación) usa la conexión
interna; cada lectura abre su propia conexión SQLite. Con `journal_mode=WAL`
eso permite leer sin bloquear el avance de la simulación.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .entities import Call

# Ruta por defecto: <raíz del proyecto>/data/vitalis_audit.db
DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "vitalis_audit.db"

CSV_HEADER = (
    "id,gravedad,x_km,y_km,t_llegada,t_asignacion,t_subida,t_fin,"
    "espera_min,respuesta_min,total_min"
)

_INSERT = (
    "INSERT INTO calls (run_id, id, gravedad, x_km, y_km, t_llegada) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_UPDATE = (
    "UPDATE calls SET t_asignacion = ?, t_subida = ?, t_fin = ?, "
    "espera_min = ?, respuesta_min = ?, total_min = ? "
    "WHERE run_id = ? AND id = ?"
)


def _num(value: Optional[float], digits: int = 2) -> str:
    """Formatea un flotante como el CSV original (vacío si es None)."""
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def _js_round(value: float) -> int:
    """`Math.round` de JS (mitad hacia arriba) para replicar el χ² del frontend."""
    return int(math.floor(value + 0.5))


class AuditStore:
    """Sink de auditoría respaldado por SQLite."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH, batch_size: int = 500) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.batch_size = max(int(batch_size), 1)
        # Búfer ordenado de operaciones: ("I", fila) o ("U", fila). El orden se
        # respeta al volcar, de modo que el INSERT precede a su UPDATE.
        self._buffer: List[Tuple[str, Tuple[Any, ...]]] = []
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    # ------------------------------------------------------------- esquema ----
    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                seed       INTEGER NOT NULL,
                started_at REAL NOT NULL,
                params     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS calls (
                run_id        INTEGER NOT NULL,
                id            INTEGER NOT NULL,
                gravedad      TEXT NOT NULL,
                x_km          REAL,
                y_km          REAL,
                t_llegada     REAL,
                t_asignacion  REAL,
                t_subida      REAL,
                t_fin         REAL,
                espera_min    REAL,
                respuesta_min REAL,
                total_min     REAL
            );
            CREATE INDEX IF NOT EXISTS idx_calls_run_t ON calls(run_id, t_llegada);
            """
        )
        self._conn.commit()

    def _reader(self) -> sqlite3.Connection:
        """Conexión de solo lectura (para no bloquear al escritor)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA query_only=ON")
        return conn

    # ------------------------------------------------------------- corridas ---
    def new_run(self, seed: int, params: Dict[str, Any]) -> int:
        """Registra una corrida nueva y devuelve su `run_id`."""
        cur = self._conn.execute(
            "INSERT INTO runs (seed, started_at, params) VALUES (?, ?, ?)",
            (int(seed), time.time(), json.dumps(params, ensure_ascii=False)),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def latest_run_id(self) -> Optional[int]:
        row = self._conn.execute("SELECT MAX(run_id) FROM runs").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    # ------------------------------------------------------------ escritura ---
    def record_arrival(self, run_id: int, call: Call) -> None:
        """Registra una llamada nueva (llegada conocida; el resto queda nulo)."""
        self._buffer.append(
            (
                "I",
                (
                    int(run_id),
                    call.id,
                    call.severity_name,
                    round(call.x, 4),
                    round(call.y, 4),
                    call.t_arrival,
                ),
            )
        )
        self._maybe_flush()

    def record_completion(self, run_id: int, call: Call) -> None:
        """Completa la fila de una llamada terminada (tiempos de servicio)."""
        self._buffer.append(
            (
                "U",
                (
                    call.t_assigned,
                    call.t_picked,
                    call.t_done,
                    call.wait_min,
                    call.response_min,
                    call.total_min,
                    int(run_id),
                    call.id,
                ),
            )
        )
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Vuelca a disco las operaciones pendientes (en orden)."""
        if not self._buffer:
            return
        try:
            for op, params in self._buffer:
                self._conn.execute(_INSERT if op == "I" else _UPDATE, params)
            self._conn.commit()
        finally:
            self._buffer.clear()

    def close(self) -> None:
        try:
            self.flush()
            self._conn.close()
        except Exception:
            pass

    # -------------------------------------------------------------- lectura ---
    def iter_csv(self, run_id: int) -> Iterator[str]:
        """Genera el CSV de la corrida línea a línea (memoria constante)."""
        conn = self._reader()
        try:
            yield CSV_HEADER + "\r\n"
            cur = conn.execute(
                "SELECT id, gravedad, x_km, y_km, t_llegada, t_asignacion, "
                "t_subida, t_fin, espera_min, respuesta_min, total_min "
                "FROM calls WHERE run_id = ? ORDER BY id",
                (int(run_id),),
            )
            for row in cur:
                yield (
                    f"{row[0]},{row[1]},{_num(row[2], 4)},{_num(row[3], 4)},"
                    f"{_num(row[4])},{_num(row[5])},{_num(row[6])},{_num(row[7])},"
                    f"{_num(row[8])},{_num(row[9])},{_num(row[10])}\r\n"
                )
        finally:
            conn.close()

    def poisson_histogram(
        self, run_id: int, delta: float, now: float = 0.0
    ) -> Optional[Dict[str, Any]]:
        """Distribución del nº de llamadas por ventana de Δ min (para el χ²).

        Replica exactamente el cálculo del frontend: ventanas de Δ min desde 0,
        `nWin = round(span/Δ)`, llamada en la ventana `floor(t/Δ)`. Devuelve
        `{total, nWin, horizon, delta, obs:[nº de ventanas con k llamadas]}`.
        Se computa en *streaming*: la RAM no depende del nº de llamadas.
        """
        delta = float(delta)
        if delta <= 0:
            raise ValueError("delta debe ser > 0")

        conn = self._reader()
        try:
            total, last_arr = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(t_llegada), 0) FROM calls WHERE run_id = ?",
                (int(run_id),),
            ).fetchone()
            total = int(total or 0)
            if total == 0:
                return None
            last_arr = float(last_arr or 0.0)
            span = max(float(now or 0.0), last_arr, delta)
            n_win = max(1, _js_round(span / delta))

            obs_map: Dict[int, int] = defaultdict(int)
            kmax = 0
            nonempty = 0
            cur_idx: Optional[int] = None
            cur_count = 0
            for (t,) in conn.execute(
                "SELECT t_llegada FROM calls WHERE run_id = ? ORDER BY t_llegada",
                (int(run_id),),
            ):
                idx = int(math.floor(float(t) / delta))
                if idx < 0:
                    idx = 0
                elif idx > n_win - 1:
                    idx = n_win - 1
                if idx != cur_idx:
                    if cur_idx is not None:
                        obs_map[cur_count] += 1
                        if cur_count > kmax:
                            kmax = cur_count
                    cur_idx = idx
                    cur_count = 1
                    nonempty += 1
                else:
                    cur_count += 1
            if cur_idx is not None:
                obs_map[cur_count] += 1
                if cur_count > kmax:
                    kmax = cur_count

            obs_map[0] += max(n_win - nonempty, 0)
            obs = [int(obs_map.get(k, 0)) for k in range(kmax + 1)]
            return {
                "total": total,
                "nWin": n_win,
                "horizon": round(span, 2),
                "delta": delta,
                "obs": obs,
            }
        finally:
            conn.close()


class RunRecorder:
    """Liga el motor a una corrida concreta del `AuditStore`.

    El motor solo conoce este objeto (`on_arrival` / `on_done`), de modo que
    `engine.py` queda desacoplado de la persistencia en disco y de la API.
    """

    __slots__ = ("_store", "_run_id")

    def __init__(self, store: AuditStore, run_id: int) -> None:
        self._store = store
        self._run_id = int(run_id)

    @property
    def run_id(self) -> int:
        return self._run_id

    def on_arrival(self, call: Call) -> None:
        self._store.record_arrival(self._run_id, call)

    def on_done(self, call: Call) -> None:
        self._store.record_completion(self._run_id, call)

    def flush(self) -> None:
        self._store.flush()
