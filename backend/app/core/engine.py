"""
FASE 2 — Motor de simulación híbrido (Rol B: Motores de Simulación).

Combina las dos dinámicas exigen la guía y la Alternativa 3:

EVENTOS DISCRETOS (calendario de eventos con heapq):
    ARRIVAL          -> nueva llamada (proceso de Poisson con tasa lambda)
    AMB_AT_CALL      -> la ambulancia llega al sitio del paciente
    AMB_LOADED       -> termina la carga en escena
    AMB_AT_HOSPITAL  -> entrega en hospital y se libera la unidad

DINÁMICA CONTINUA (Rol A):
    la posición de cada ambulancia se interpola linealmente entre eventos
    (x' = v·cosθ, y' = v·sinθ resuelto por trayectoria recta), y el tiempo
    de viaje proviene de la distancia euclídea:
        t_viaje = (distancia_km / velocidad_kmh) · 60 · coef_tráfico

CONTROL EN VIVO (sustentación):
    set_params() modifica lambda, velocidad, coef. de tráfico, tamaño de
    flota o reloj SIN reiniciar; cambiar lambda reprograma la próxima
    llegada de inmediato. reset(seed) reproduce una corrida exacta.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .distributions import exponential_interarrival
from .entities import (
    SEVERITY_WEIGHTS,
    AmbStatus,
    Ambulance,
    Base,
    Call,
)
from .geo import km_to_latlng, latlng_to_km
from .prng import DEFAULT_SEED, PRNG
from .stats import SimStats

# Tipos de evento del calendario
EVT_ARRIVAL = "ARRIVAL"
EVT_AT_CALL = "AMB_AT_CALL"
EVT_LOADED = "AMB_LOADED"
EVT_AT_HOSPITAL = "AMB_AT_HOSPITAL"

# Tope de seguridad por avance (evita bucles infinitos con lambda extremo)
MAX_EVENTS_PER_ADVANCE = 50_000

# Máximo de llamadas en espera que viajan en cada snapshot (las más urgentes).
# El KPI "En espera" sigue contando la cola completa: esto solo acota el
# tamaño del JSON que se envía 10 veces por segundo al navegador.
MAX_WAITING_DRAWN = 150

# Contador global de corridas: cada SimEngine y cada reset reciben un id nuevo.
# La GUI lo usa para descartar estados que lleguen fuera de orden.
_RUN_SEQ = 0


def _next_run_id() -> int:
    global _RUN_SEQ
    _RUN_SEQ += 1
    return _RUN_SEQ

# Centro de la ciudad por defecto (Roldanillo, Valle del Cauca) ------------
DEFAULT_CITY_LAT = 4.41361
DEFAULT_CITY_LNG = -76.1547

# Configuración por defecto: un hospital que atiende pacientes y una estación
# de bomberos que solo despacha ambulancias (no recibe traslados).
DEFAULT_BASES_SPEC: List[Dict[str, Any]] = [
    {
        "name": "Hospital Departamental San Antonio",
        "lat": 4.404525,
        "lng": -76.152172,
        "num_ambulances": 4,
        "is_hospital": True,
    },
    {
        "name": "Cuerpo de Bomberos Voluntarios",
        "lat": 4.407223,
        "lng": -76.152638,
        "num_ambulances": 2,
        "is_hospital": False,
    },
]


def _default_bases_spec() -> List[Dict[str, Any]]:
    return [dict(spec) for spec in DEFAULT_BASES_SPEC]


@dataclass
class SimConfig:
    """Parámetros de la simulación (todos alterables en vivo)."""

    seed: int = DEFAULT_SEED
    lambda_per_min: float = 0.15  # llamadas de emergencia por minuto (Poisson)
    speed_kmh: float = 42.0  # velocidad base del vehículo (Rol A)
    traffic_factor: float = 1.0  # coeficiente físico de tráfico (>=1, defensa)
    load_time_min: float = 6.0  # tiempo medio en escena
    city_radius_km: float = 6.0  # radio de cobertura de la ciudad
    clock_speed: float = 1.0  # min simulados por segundo real
    city_center_lat: float = DEFAULT_CITY_LAT  # centro del área de cobertura
    city_center_lng: float = DEFAULT_CITY_LNG
    ambulance_bases: List[Dict[str, Any]] = field(default_factory=_default_bases_spec)

    def public_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SimEngine:
    """Motor híbrido: reloj de simulación + calendario de eventos + flujo continuo."""

    def __init__(self, config: Optional[SimConfig] = None, audit: Optional[Any] = None) -> None:
        self.config = config or SimConfig()
        # Sink de auditoría (opcional): objeto con `.on_arrival(call)` y
        # `.on_done(call)`. Con un sink, las llamadas terminadas se persisten
        # fuera de la RAM y se liberan de la memoria del motor (corridas largas).
        self.audit = audit
        self._init_run(self.config.seed)

    # ------------------------------------------------------------- arranque ---
    def _init_run(self, seed: int) -> None:
        self.run_id = _next_run_id()
        self.config.seed = int(seed)
        self.prng = PRNG(self.config.seed)
        self.now = 0.0
        self.stats = SimStats()
        self.calls_by_id: Dict[int, Call] = {}
        self.waiting: List[Call] = []  # heap: prioridad por gravedad, luego FIFO
        self.ambulances: List[Ambulance] = []
        self.amb_by_id: Dict[int, Ambulance] = {}
        self.busy_count = 0
        self._events: List[tuple] = []
        self._seq = 0
        self._cancelled: set = set()
        self._arrival_seq: Optional[int] = None
        self._next_call_id = 1
        self._build_bases()
        self._build_fleet()
        self._schedule_next_arrival()

    # ------------------------------------------------------------ bases -------
    def _validate_bases(self, specs: List[Dict[str, Any]]) -> None:
        """Una base por defecto y al menos un hospital son obligatorios."""
        if not specs:
            raise ValueError("se requiere al menos una base de ambulancias")
        if not any(bool(s.get("is_hospital", True)) for s in specs):
            raise ValueError("al menos una base debe atender pacientes (is_hospital=True)")

    def _build_bases(self) -> None:
        """(Re)construye las bases desde la configuración (lat/lng -> km relativos)."""
        specs = self.config.ambulance_bases or []
        self._validate_bases(specs)
        self.bases: List[Base] = []
        clat, clng = self.config.city_center_lat, self.config.city_center_lng
        for i, spec in enumerate(specs):
            x, y = latlng_to_km(float(spec["lat"]), float(spec["lng"]), clat, clng)
            self.bases.append(
                Base(
                    id=i,
                    name=str(spec.get("name") or f"Base {i + 1}"),
                    x=x,
                    y=y,
                    num_ambulances=max(int(spec.get("num_ambulances", 1)), 0),
                    is_hospital=bool(spec.get("is_hospital", True)),
                )
            )

    def _build_fleet(self) -> None:
        """Crea la flota total aparcada en la posición de cada base."""
        self.ambulances.clear()
        self.amb_by_id.clear()
        counter = 0
        for base in self.bases:
            for _ in range(base.num_ambulances):
                amb = Ambulance(
                    id=counter,
                    base_id=base.id,
                    fx=base.x,
                    fy=base.y,
                    tx=base.x,
                    ty=base.y,
                )
                self.ambulances.append(amb)
                self.amb_by_id[amb.id] = amb
                counter += 1

    # ------------------------------------------------------- calendario event.-
    def _schedule(self, when: float, kind: str, payload: Dict[str, Any]) -> int:
        self._seq += 1
        heapq.heappush(self._events, (when, self._seq, kind, payload))
        return self._seq

    def _schedule_next_arrival(self) -> None:
        rate = self.config.lambda_per_min
        if rate <= 0:
            self._arrival_seq = None
            return
        delay = exponential_interarrival(self.prng, rate)
        self._arrival_seq = self._schedule(self.now + delay, EVT_ARRIVAL, {})

    def _reschedule_next_arrival(self) -> None:
        """Cancela la próxima llegada y la reprograma con el lambda vigente."""
        if self._arrival_seq is not None:
            self._cancelled.add(self._arrival_seq)
            self._arrival_seq = None
        self._schedule_next_arrival()

    # ------------------------------------------------------------ reloj ------
    def advance(self, until: float) -> int:
        """Avanza el reloj hasta `until` procesando todos los eventos previos."""
        if until < self.now:
            raise ValueError("el reloj de simulación no puede retroceder")
        processed = 0
        while self._events and self._events[0][0] <= until:
            if processed >= MAX_EVENTS_PER_ADVANCE:
                break
            t, seq, kind, payload = heapq.heappop(self._events)
            if seq in self._cancelled:
                self._cancelled.discard(seq)
                continue
            # acumula promedios continuos con el estado PREVIO al evento
            self.stats.advance_to(t, len(self.waiting), self.busy_count)
            self.now = t
            self._dispatch_event(kind, payload)
            processed += 1
        self.now = until
        self.stats.advance_to(until, len(self.waiting), self.busy_count)
        return processed

    def _dispatch_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind == EVT_ARRIVAL:
            self._on_arrival()
        elif kind == EVT_AT_CALL:
            self._on_at_call(payload)
        elif kind == EVT_LOADED:
            self._on_loaded(payload)
        elif kind == EVT_AT_HOSPITAL:
            self._on_at_hospital(payload)

    # ----------------------------------------------------- manejadores --------
    def _on_arrival(self) -> None:
        self._arrival_seq = None
        call = self._create_call()
        self._try_assign()
        self._schedule_next_arrival()  # proceso de Poisson: siguiente exponencial

    def _create_call(self) -> Call:
        cid = self._next_call_id
        self._next_call_id += 1
        # Distribución uniforme sobre el disco de cobertura (área = pi·R²)
        angle = self.prng.next() * 2.0 * math.pi
        radius = self.config.city_radius_km * math.sqrt(self.prng.next())
        severity = int(self.prng.weighted_choice((0, 1, 2, 3), SEVERITY_WEIGHTS))
        call = Call(
            id=cid,
            x=radius * math.cos(angle),
            y=radius * math.sin(angle),
            severity=severity,
            t_arrival=self.now,
        )
        self.calls_by_id[cid] = call
        heapq.heappush(self.waiting, call)  # entra a la cola por prioridad
        self.stats.on_arrival(call)
        self.stats.observe_queue(len(self.waiting))
        # Auditoría: registra la llegada en disco (la fila se completará al terminar).
        if self.audit is not None:
            try:
                self.audit.on_arrival(call)
            except Exception:  # la auditoría nunca debe tumbar la simulación
                pass
        return call

    def _try_assign(self) -> None:
        """Asigna la unidad libre más cercana a la llamada de mayor prioridad."""
        while self.waiting:
            idle = [a for a in self.ambulances if a.status == AmbStatus.IDLE]
            if not idle:
                break
            call = heapq.heappop(self.waiting)
            amb = min(idle, key=lambda a: self._sq_dist_to(a, call))
            self._dispatch(amb, call)

    def _sq_dist_to(self, amb: Ambulance, call: Call) -> float:
        """Distancia euclídea al cuadrado desde la posición ACTUAL de la unidad."""
        x, y = amb.position_at(self.now)
        return (x - call.x) ** 2 + (y - call.y) ** 2

    def _dispatch(self, amb: Ambulance, call: Call) -> None:
        call.t_assigned = self.now
        call.amb_id = amb.id
        amb.status = AmbStatus.EN_ROUTE_TO_CALL
        amb.call_id = call.id
        px, py = amb.position_at(self.now)
        travel = self._travel_time(px, py, call.x, call.y)
        amb.set_path(call.x, call.y, self.now, travel)
        self.busy_count += 1
        self.stats.on_assign(call)
        self._schedule(self.now + travel, EVT_AT_CALL, {"amb": amb.id, "call": call.id})

    def _on_at_call(self, payload: Dict[str, Any]) -> None:
        amb = self.amb_by_id[payload["amb"]]
        call = self.calls_by_id[payload["call"]]
        amb.set_path(call.x, call.y, self.now, 0.0)  # estacionado en el sitio
        amb.status = AmbStatus.AT_SCENE
        # duración en escena con variación aleatoria ±25 %
        load = self.config.load_time_min * (0.75 + 0.5 * self.prng.next())
        self._schedule(self.now + load, EVT_LOADED, {"amb": amb.id, "call": call.id})

    def _on_loaded(self, payload: Dict[str, Any]) -> None:
        amb = self.amb_by_id[payload["amb"]]
        call = self.calls_by_id[payload["call"]]
        # Paciente a bordo: fin de la carga en escena. El tiempo de respuesta
        # (t_picked - t_arrival) incluye espera + viaje al sitio + escena.
        if call.t_picked is None:
            call.t_picked = self.now
            self.stats.on_pick(call)
        amb.status = AmbStatus.EN_ROUTE_TO_HOSPITAL
        amb.call_id = call.id
        px, py = amb.position_at(self.now)
        # destino: el HOSPITAL (base que atiende) MÁS CERCANO a la posición actual
        hosp = self._nearest_hospital(px, py)
        travel = self._travel_time(px, py, hosp.x, hosp.y)
        amb.set_path(hosp.x, hosp.y, self.now, travel)
        self._schedule(
            self.now + travel,
            EVT_AT_HOSPITAL,
            {"amb": amb.id, "call": call.id, "hx": hosp.x, "hy": hosp.y},
        )

    def _nearest_hospital(self, x: float, y: float) -> Base:
        hospitals = [b for b in self.bases if b.is_hospital]
        if not hospitals:
            raise ValueError("no hay hospitales configurados para recibir pacientes")
        return min(hospitals, key=lambda b: (b.x - x) ** 2 + (b.y - y) ** 2)

    def _on_at_hospital(self, payload: Dict[str, Any]) -> None:
        amb = self.amb_by_id[payload["amb"]]
        call = self.calls_by_id[payload["call"]]
        hx, hy = payload.get("hx") or 0.0, payload.get("hy") or 0.0
        amb.set_path(hx, hy, self.now, 0.0)
        amb.status = AmbStatus.IDLE
        amb.call_id = None
        call.t_done = self.now
        self.stats.on_done(call)
        self.busy_count -= 1
        # Auditoría persistente: completa la fila de la llamada y libérala de la
        # RAM. Así la memoria del motor queda acotada (solo activas/en cola) y
        # la corrida puede durar indefinidamente.
        if self.audit is not None:
            try:
                self.audit.on_done(call)
            except Exception:  # la auditoría nunca debe tumbar la simulación
                pass
        self.calls_by_id.pop(call.id, None)
        retire = amb.retire_on_release
        if retire:
            self._remove_ambulance(amb)
        self._try_assign()  # atiende la siguiente llamada en cola

    # ------------------------------------------------------------ física ------
    def _travel_time(self, x1: float, y1: float, x2: float, y2: float) -> float:
        """Tiempo de viaje continuo en minutos (distancia euclídea + tráfico)."""
        dist = math.hypot(x2 - x1, y2 - y1)
        speed_kmh = max(self.config.speed_kmh, 0.1)
        return (dist / speed_kmh) * 60.0 * self.config.traffic_factor

    # -------------------------------------------------- control en vivo -------
    def set_params(self, **kwargs: Any) -> Dict[str, Any]:
        """Actualiza parámetros sin reiniciar la corrida (defensa en caliente)."""
        cfg = self.config
        applied: Dict[str, Any] = {}

        if kwargs.get("lambda_per_min") is not None:
            value = float(kwargs["lambda_per_min"])
            if value <= 0:
                raise ValueError("lambda_per_min debe ser > 0")
            if value != cfg.lambda_per_min:
                cfg.lambda_per_min = value
                self._reschedule_next_arrival()  # el efecto es INMEDIATO
            applied["lambda_per_min"] = value

        bases_given = kwargs.get("ambulance_bases") is not None
        center_given = kwargs.get("city_center_lat") is not None or kwargs.get("city_center_lng") is not None
        if bases_given:
            specs = [dict(b) for b in kwargs["ambulance_bases"]]
            self._validate_bases(specs)  # falla ANTES de mutar la configuración
            cfg.ambulance_bases = specs
        if center_given:
            clat = float(kwargs.get("city_center_lat", cfg.city_center_lat))
            clng = float(kwargs.get("city_center_lng", cfg.city_center_lng))
            if not (-90.0 <= clat <= 90.0 and -180.0 <= clng <= 180.0):
                raise ValueError("coordenadas de centro fuera de rango")
            cfg.city_center_lat = clat
            cfg.city_center_lng = clng
        if bases_given or center_given:
            self._build_bases()  # valida y recalcula posiciones
            self._reconcile_fleet()  # alta/baja/modificación de unidades en vivo
            applied["ambulance_bases"] = cfg.ambulance_bases
            applied["city_center_lat"] = cfg.city_center_lat
            applied["city_center_lng"] = cfg.city_center_lng

        for key in ("speed_kmh", "traffic_factor", "load_time_min", "clock_speed", "city_radius_km"):
            if kwargs.get(key) is not None:
                value = float(kwargs[key])
                if value <= 0:
                    raise ValueError(f"{key} debe ser > 0")
                setattr(cfg, key, value)
                applied[key] = value
        return applied

    # ------------------------------------------- conciliación de flota ---------
    def _add_ambulance(self, amb: Ambulance) -> None:
        self.ambulances.append(amb)
        self.amb_by_id[amb.id] = amb

    def _remove_ambulance(self, amb: Ambulance) -> None:
        if amb in self.ambulances:
            self.ambulances.remove(amb)
        self.amb_by_id.pop(amb.id, None)

    def _reconcile_fleet(self) -> None:
        """Ajusta la flota a la configuración de bases SIN reiniciar la corrida.

        - Alta de base        -> se crean las ambulancias en su posición.
        - Baja / reducción    -> las unidades LIBRES se retiran al instante;
                                 las OCUPADAS terminan su misión y se retiran
                                 al liberarse (retire_on_release).
        """
        base_ids = {b.id for b in self.bases}
        to_remove: List[Ambulance] = []
        new_units: List[Ambulance] = []
        amb_id = (max((a.id for a in self.ambulances), default=-1)) + 1

        for base in self.bases:
            owned = [a for a in self.ambulances if a.base_id == base.id]
            for a in owned:
                a.retire_on_release = False
            target = max(int(base.num_ambulances), 0)
            diff = len(owned) - target
            if diff > 0:
                for a in [x for x in owned if x.status == AmbStatus.IDLE]:
                    if diff == 0:
                        break
                    to_remove.append(a)
                    diff -= 1
                for a in [x for x in owned if x.status != AmbStatus.IDLE]:
                    if diff == 0:
                        break
                    a.retire_on_release = True
                    diff -= 1
            elif diff < 0:
                for _ in range(-diff):
                    new_units.append(
                        Ambulance(
                            id=amb_id,
                            base_id=base.id,
                            fx=base.x,
                            fy=base.y,
                            tx=base.x,
                            ty=base.y,
                        )
                    )
                    amb_id += 1

        # bases que ya no existen: libres se retiran, ocupadas al finalizar
        for amb in list(self.ambulances):
            if amb.base_id not in base_ids:
                if amb.status == AmbStatus.IDLE:
                    to_remove.append(amb)
                else:
                    amb.retire_on_release = True

        for amb in to_remove:
            self._remove_ambulance(amb)
        for amb in new_units:
            self._add_ambulance(amb)

    def reset(self, seed: Optional[int] = None) -> None:
        """Reinicia la corrida. Misma semilla => misma secuencia exacta."""
        self._init_run(self.config.seed if seed is None else int(seed))

    # ------------------------------------------------------------ snapshot ----
    def snapshot(self) -> Dict[str, Any]:
        """Estado completo para la API / la animación en vivo."""
        t = self.now
        self.stats.advance_to(t, len(self.waiting), self.busy_count)

        ambulances = []
        for amb in self.ambulances:
            x, y = amb.position_at(t)
            moving = amb.is_moving(t)
            ambulances.append(
                {
                    "id": amb.id,
                    "x": round(x, 4),
                    "y": round(y, 4),
                    "status": amb.status.value,
                    "call_id": amb.call_id,
                    "target": {"x": round(amb.tx, 4), "y": round(amb.ty, 4)} if moving else None,
                }
            )

        waiting_total = len(self.waiting)
        waiting = [
            {
                "id": c.id,
                "x": round(c.x, 4),
                "y": round(c.y, 4),
                "severity": c.severity,
                "severity_name": c.severity_name,
                "wait_min": round(t - c.t_arrival, 2),
            }
            for c in heapq.nsmallest(
                MAX_WAITING_DRAWN,
                self.waiting,
                key=lambda c: (c.severity, c.t_arrival, c.id),
            )
        ]

        # Las misiones en curso son, por definición, las ambulancias ocupadas:
        # recorrer la flota cuesta O(c) y no O(nº de llamadas de la corrida).
        active = []
        for amb in self.ambulances:
            if amb.call_id is None:
                continue
            c = self.calls_by_id.get(amb.call_id)
            if c is None or c.t_done is not None:
                continue
            if amb.status == AmbStatus.AT_SCENE:
                estado = "en_escena"
            elif c.t_picked is None:
                estado = "en_camino"
            else:
                estado = "traslado"
            active.append(
                {
                    "id": c.id,
                    "x": round(c.x, 4),
                    "y": round(c.y, 4),
                    "severity": c.severity,
                    "severity_name": c.severity_name,
                    "status": estado,
                    "amb_id": c.amb_id,
                    "elapsed_min": round(t - c.t_arrival, 2),
                }
            )

        clat, clng = self.config.city_center_lat, self.config.city_center_lng
        bases = []
        for base in self.bases:
            owned = [a for a in self.ambulances if a.base_id == base.id]
            bases.append(
                {
                    "id": base.id,
                    "name": base.name,
                    "x": round(base.x, 4),
                    "y": round(base.y, 4),
                    "lat": round(km_to_latlng(base.x, base.y, clat, clng)[0], 6),
                    "lng": round(km_to_latlng(base.x, base.y, clat, clng)[1], 6),
                    "num_ambulances": base.num_ambulances,
                    "is_hospital": base.is_hospital,
                    "available": sum(1 for a in owned if a.status == AmbStatus.IDLE),
                    "busy": sum(1 for a in owned if a.status != AmbStatus.IDLE),
                }
            )

        return {
            "now": round(t, 2),
            "seed": self.config.seed,
            "params": self.config.public_dict(),
            "bases": bases,
            "ambulances": ambulances,
            "waiting_calls": waiting,
            "waiting_total": waiting_total,
            "active_calls": active,
            "stats": self.stats.summary(
                now=t,
                queue_len=len(self.waiting),
                busy_count=self.busy_count,
                num_ambulances=len(self.ambulances),
            ),
        }
