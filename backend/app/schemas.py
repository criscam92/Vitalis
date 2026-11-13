"""Esquemas de la API (Pydantic) — validación de parámetros de simulación."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from .core.engine import DEFAULT_CITY_LAT, DEFAULT_CITY_LNG, DEFAULT_BASES_SPEC
from .core.prng import DEFAULT_SEED


class BaseSpec(BaseModel):
    """Punto logístico: estación de ambulancias y/o hospital configurable."""

    name: str = Field(default="Base", min_length=1, max_length=80)
    lat: float = Field(default=DEFAULT_CITY_LAT, ge=-90, le=90)
    lng: float = Field(default=DEFAULT_CITY_LNG, ge=-180, le=180)
    num_ambulances: int = Field(default=1, ge=0, le=30, description="Flota asignada a esta base")
    is_hospital: bool = Field(
        default=True,
        description="True = atiende pacientes (recibe traslados); False = solo estación de despacho",
    )


def _default_bases() -> List[BaseSpec]:
    return [BaseSpec(**spec) for spec in DEFAULT_BASES_SPEC]


class StartRequest(BaseModel):
    seed: int = Field(default=DEFAULT_SEED, description="Semilla del PRNG (corrida reproducible)")
    lambda_per_min: float = Field(default=0.15, gt=0, le=5, description="Llamadas Poisson por minuto")
    ambulance_bases: List[BaseSpec] = Field(default_factory=_default_bases)
    speed_kmh: float = Field(default=42.0, gt=0, le=200, description="Velocidad base km/h")
    traffic_factor: float = Field(
        default=1.0, gt=0, le=5, description="Coeficiente físico de tráfico (>=1 = más lento)"
    )
    load_time_min: float = Field(default=6.0, gt=0, le=60, description="Tiempo medio en escena")
    city_radius_km: float = Field(default=6.0, gt=0.5, le=30, description="Radio de cobertura")
    clock_speed: float = Field(
        default=1.0, gt=0, le=120, description="Minutos simulados por segundo real"
    )
    city_center_lat: float = Field(
        default=DEFAULT_CITY_LAT, ge=-90, le=90, description="Centro del área de cobertura (lat)"
    )
    city_center_lng: float = Field(
        default=DEFAULT_CITY_LNG, ge=-180, le=180, description="Centro del área de cobertura (lng)"
    )


class ParamsPatch(BaseModel):
    """Cambios EN CALIENTE (no reinician la corrida)."""

    lambda_per_min: Optional[float] = Field(default=None, gt=0, le=5)
    ambulance_bases: Optional[List[BaseSpec]] = Field(default=None)
    speed_kmh: Optional[float] = Field(default=None, gt=0, le=200)
    traffic_factor: Optional[float] = Field(default=None, gt=0, le=5)
    load_time_min: Optional[float] = Field(default=None, gt=0, le=60)
    city_radius_km: Optional[float] = Field(default=None, gt=0.5, le=30)
    clock_speed: Optional[float] = Field(default=None, gt=0, le=120)
    city_center_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    city_center_lng: Optional[float] = Field(default=None, ge=-180, le=180)


class StepRequest(BaseModel):
    """Avance manual del reloj (útil con la simulación en pausa)."""

    minutes: float = Field(default=5.0, gt=0, le=240)


class ResetRequest(BaseModel):
    """Reinicio de corrida; cambiar la semilla altera toda la trayectoria."""

    seed: Optional[int] = None