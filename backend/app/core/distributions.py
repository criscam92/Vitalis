"""
FASE 1/2 — Distribuciones de probabilidad (Rol B + Rol A).

El intervalo entre llegadas de un proceso de Poisson se obtiene por
transformación inversa sobre los U(0,1) del PRNG:

  * exponencial -> tiempos entre llegadas de un proceso de Poisson (lambda)
"""

from __future__ import annotations

import math

from .prng import PRNG


def exponential(prng: PRNG, rate: float) -> float:
    """Variable exponencial con parámetro `rate` (eventos por unidad de tiempo).

    Método de transformación inversa:  X = -ln(U) / lambda,  U ~ U(0,1).
    En un proceso de Poisson los tiempos entre llegadas son exponenciales.
    """
    if rate <= 0:
        raise ValueError("rate debe ser > 0")
    u = prng.next()
    return -math.log(u) / rate


# intervalo entre llegadas de un proceso de Poisson.
exponential_interarrival = exponential
