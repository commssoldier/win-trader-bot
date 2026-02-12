"""Classificação de regime de mercado."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RegimeSignal:
    regime: str
    direction: str
    details: str


class RegimeDetector:
    """Classifica tendência, lateralidade e extrema volatilidade."""

    def classify(
        self,
        adx15: float,
        ema20: float,
        ema50: float,
        limit_trend: float,
        limit_range: float,
        range20: float,
        vol_extreme: bool,
    ) -> RegimeSignal:
        if vol_extreme:
            return RegimeSignal("PAUSADO", "NEUTRO", "Volatilidade extrema")

        if adx15 > limit_trend:
            if ema20 > ema50:
                return RegimeSignal("TENDENCIA", "COMPRA", "ADX forte e EMA20>EMA50")
            if ema20 < ema50:
                return RegimeSignal("TENDENCIA", "VENDA", "ADX forte e EMA20<EMA50")

        if adx15 < limit_range and range20 > 0:
            return RegimeSignal("LATERAL", "NEUTRO", "ADX baixo e range definido")

        return RegimeSignal("NEUTRO", "NEUTRO", "Sem contexto claro")
