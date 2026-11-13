"""
Georreferenciación lat/lng <-> km del plano de simulación (Rol A / Rol C).

Espejo EXACTO de la proyección usada en el frontend (frontend/js/map.js):

    y_km = (lat - center_lat) * 110.574
    x_km = (lng - center_lng) * 111.32 * cos(center_lat)

Las constantes provienen de la aproximación esférica local de la Tierra
(radio medio ~6371 km):
    * 110.574 km por grado de latitud,
    * 111.32 km por grado de longitud en el ecuador, corregida por el
      coseno de la latitud (los meridianos convergen hacia los polos).

IMPORTANTE: el backend (aquí) y el frontend (map.js) deben usar SIEMPRE
la misma fórmula para que lat/lng <-> km sean reversibles sin error.
"""

from __future__ import annotations

import math
from typing import Tuple

KM_PER_DEG_LAT = 110.574  # km por grado de latitud
KM_PER_DEG_LNG_EQUATOR = 111.32  # km por grado de longitud en el ecuador


def _lng_scale(center_lat: float) -> float:
    """Km por grado de longitud a la latitud dada (convergencia de meridianos)."""
    return KM_PER_DEG_LNG_EQUATOR * math.cos(math.radians(center_lat))


def latlng_to_km(lat: float, lng: float, center_lat: float, center_lng: float) -> Tuple[float, float]:
    """Convierte lat/lng absolutas a coordenadas (km) relativas al centro de ciudad."""
    y = (lat - center_lat) * KM_PER_DEG_LAT
    x = (lng - center_lng) * _lng_scale(center_lat)
    return x, y


def km_to_latlng(x: float, y: float, center_lat: float, center_lng: float) -> Tuple[float, float]:
    """Inverso de latlng_to_km: coordenas (km) -> lat/lng absolutas."""
    lat = center_lat + y / KM_PER_DEG_LAT
    lng = center_lng + x / _lng_scale(center_lat)
    return lat, lng