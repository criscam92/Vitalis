"""
FASE 2 — Entidades del dominio (llamadas, ambulancias, bases/hospitales).

Rol A (modelado físico) define aquí la cinemática del vehículo:
la posición se interpola linealmente entre dos eventos discretos, de modo
que entre eventos el movimiento es CONTINUO (lista para la animación del
mapa) mientras el reloj de simulación salta de evento en evento.

Base = punto logístico configurable. Puede ser:
    * Hospital  (is_hospital=True):  recibe a los pacientes trasladados.
    * Estación  (is_hospital=False): aporta ambulancias al despacho pero
      NO recibe pacientes; sus unidades llevan al enfermo al HOSPITAL MÁS
      CERCANO.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

# Niveles de gravedad (triage) -----------------------------------------------
SEVERITY_NAMES = ("Rojo", "Naranja", "Amarillo", "Verde")
# Pesos de ocurrencia: lo crítico es lo más raro (y lo más prioritario).
SEVERITY_WEIGHTS = (0.08, 0.22, 0.35, 0.35)


class AmbStatus(str, Enum):
    """Estado lógico de una ambulancia (evento discreto actual)."""

    IDLE = "libre"
    EN_ROUTE_TO_CALL = "en_camino"
    AT_SCENE = "en_escena"
    EN_ROUTE_TO_HOSPITAL = "traslado"


@dataclass
class Call:
    """Llamada de emergencia entrante."""

    id: int
    x: float
    y: float
    severity: int  # 0 = Rojo (crítica) ... 3 = Verde (leve)
    t_arrival: float  # min de simulación en que llega la llamada
    t_assigned: Optional[float] = None  # recibe ambulancia
    t_picked: Optional[float] = None  # paciente a bordo
    t_done: Optional[float] = None  # llega al hospital
    amb_id: Optional[int] = None

    def __lt__(self, other: "Call") -> bool:
        """Prioridad de la cola: gravedad primero, luego orden de llegada (FIFO)."""
        return (self.severity, self.t_arrival, self.id) < (
            other.severity,
            other.t_arrival,
            other.id,
        )

    @property
    def wait_min(self) -> Optional[float]:
        """Tiempo en cola hasta que se le asigna ambulancia."""
        if self.t_assigned is None:
            return None
        return self.t_assigned - self.t_arrival

    @property
    def response_min(self) -> Optional[float]:
        """Tiempo de respuesta: llegada de la llamada -> paciente a bordo."""
        if self.t_picked is None:
            return None
        return self.t_picked - self.t_arrival

    @property
    def total_min(self) -> Optional[float]:
        """Tiempo total: llamada -> hospital."""
        if self.t_done is None:
            return None
        return self.t_done - self.t_arrival

    @property
    def severity_name(self) -> str:
        return SEVERITY_NAMES[self.severity]


@dataclass
class Ambulance:
    """Vehículo de despacho con trayectoria continua por tramos.

    El movimiento se define con un tramo activo [t0 -> t1] desde (fx,fy)
    hasta (tx,ty). Fuera del tramo el vehículo está estacionario.
    """

    id: int
    fx: float = 0.0  # origen del tramo actual
    fy: float = 0.0
    tx: float = 0.0  # destino del tramo actual
    ty: float = 0.0
    t0: float = 0.0  # inicio del tramo (min simulados)
    t1: float = 0.0  # fin del tramo (min simulados)
    status: AmbStatus = AmbStatus.IDLE
    call_id: Optional[int] = None
    base_id: Optional[int] = None  # base (estación/hospital) a la que pertenece
    retire_on_release: bool = False  # retirar la unidad al completar su misión

    def set_path(self, tx: float, ty: float, t_start: float, duration: float) -> None:
        """Define el siguiente tramo de movimiento (duración >= 0)."""
        x, y = self.position_at(t_start)
        self.fx, self.fy = float(x), float(y)
        self.tx, self.ty = float(tx), float(ty)
        self.t0 = float(t_start)
        self.t1 = float(t_start) + max(float(duration), 0.0)

    def position_at(self, t: float) -> Tuple[float, float]:
        """Interpolación lineal de la posición en el instante t (cinemática)."""
        if self.t1 <= self.t0 or t <= self.t0:
            return (self.fx, self.fy)
        if t >= self.t1:
            return (self.tx, self.ty)
        u = (t - self.t0) / (self.t1 - self.t0)
        return (self.fx + u * (self.tx - self.fx), self.fy + u * (self.ty - self.fy))

    def is_moving(self, t: float) -> bool:
        return self.t1 > self.t0 and self.t0 < t < self.t1


@dataclass(frozen=True)
class Base:
    """Punto logístico configurable: estación de ambulancias y/o hospital.
    * `is_hospital=True`  — el punto RECIBE pacientes (destino de los traslados).
    * `is_hospital=False` — solo estación de despacho de ambulancias.
    `num_ambulances` es la flota asignada a esta base (se aparca aquí al iniciar).
    """

    id: int
    name: str
    x: float = 0.0
    y: float = 0.0
    num_ambulances: int = 1
    is_hospital: bool = True
