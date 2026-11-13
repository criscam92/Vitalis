"""
FASE 1 — Generador de números pseudoaleatorios (Rol B: Ingeniería de Software).

Implementa el generador congruencial multiplicativo lineal de Park-Miller
(1969) normalizado al intervalo abierto U(0,1).

"""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")

# Parámetros del LCG de Park-Miller
M = 2_147_483_647  # 2**31 - 1 (primo de Mersenne)
A = 16_807  # 7**5

DEFAULT_SEED = 20261007


class PRNG:
    """Generador congruencial lineal de Park-Miller normalizado a U(0,1)."""

    __slots__ = ("_state",)

    def __init__(self, seed: int = DEFAULT_SEED) -> None:
        self.seed(seed)

    # ------------------------------------------------------------------ semilla
    def seed(self, seed: int) -> None:
        """Fija la semilla y reinicia la secuencia."""
        s = int(seed) % M
        if s == 0:
            s = 1  # el estado 0 es punto fijo del LCG: se evita
        self._state = s

    # --------------------------------------------------------------- generación
    def next(self) -> float:
        """Siguiente número pseudoaleatorio en el intervalo abierto U(0,1)."""
        self._state = (A * self._state) % M
        return self._state / M

    def weighted_choice(self, values: Sequence[T], weights: Sequence[float]) -> T:
        """Selección discreta por transformación inversa sobre pesos."""
        if len(values) != len(weights) or not values:
            raise ValueError("values y weights deben tener la misma longitud > 0")
        total = float(sum(weights))
        if total <= 0:
            raise ValueError("la suma de pesos debe ser > 0")
        r = self.next() * total
        acc = 0.0
        for value, weight in zip(values, weights):
            acc += weight
            if r < acc:
                return value
        return values[-1]  # tolerancia por redondeo flotante
