"""Classificação hierárquica de regime de mercado (60m + 15m + 5m timing)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class RegimeSignal:
    macro: str
    context15: str
    regime: str
    direction: str
    details: str


class RegimeDetector:
    """Classifica regime com arquitetura determinística hierárquica."""

    def __init__(self, debug_mode: bool = False, debug_callback: Callable[[str], None] | None = None) -> None:
        self.debug_mode = debug_mode
        self._debug_callback = debug_callback

    def _debug(self, message: str) -> None:
        if not self.debug_mode:
            return
        payload = f"[DEBUG] {message}"
        if self._debug_callback:
            self._debug_callback(payload)

    @staticmethod
    def ema_distance_relative_atr(ema_fast: float, ema_slow: float, atr: float) -> float:
        """Retorna distância relativa das EMAs em unidades de ATR."""
        if atr <= 0:
            return 0.0
        return abs(ema_fast - ema_slow) / atr

    @staticmethod
    def ema_slope_relative_atr(ema_now: float, ema_prev_n: float, atr: float) -> float:
        """Retorna inclinação relativa da EMA em unidades de ATR."""
        if atr <= 0:
            return 0.0
        return (ema_now - ema_prev_n) / atr

    def classify_macro(self, adx60: float, ema20_60: float, ema50_60: float, atr60: float, ema20_60_prev3: float) -> str:
        dist_rel = self.ema_distance_relative_atr(ema20_60, ema50_60, atr60)
        slope_rel = self.ema_slope_relative_atr(ema20_60, ema20_60_prev3, atr60)

        self._debug(
            f"Macro60 | ADX60={adx60:.2f} ATR60={atr60:.2f} dist_rel={dist_rel:.4f} slope_rel={slope_rel:.4f}"
        )

        macro_tendencia = adx60 > 25 and dist_rel > 0.15 and abs(slope_rel) > 0.2
        macro_lateral = adx60 < 20 or dist_rel < 0.1

        if macro_tendencia:
            return "MACRO_TENDENCIA"
        if macro_lateral:
            return "MACRO_LATERAL"
        return "MACRO_TRANSICAO"

    def classify_context15(
        self,
        adx15: float,
        ema20_15: float,
        ema50_15: float,
        atr15: float,
        ema20_15_prev3: float,
        atr15_mean20: float,
        atr15_prev: float,
    ) -> str:
        dist_rel = self.ema_distance_relative_atr(ema20_15, ema50_15, atr15)
        slope_rel = self.ema_slope_relative_atr(ema20_15, ema20_15_prev3, atr15)
        atr_expansion = atr15 > atr15_prev and atr15 > atr15_mean20

        self._debug(
            "Ctx15 | "
            f"ADX15={adx15:.2f} ATR15={atr15:.2f} ATR15_mean20={atr15_mean20:.2f} ATR15_prev={atr15_prev:.2f} "
            f"dist_rel={dist_rel:.4f} slope_rel={slope_rel:.4f} atr_expansion={atr_expansion}"
        )

        if adx15 > 25 and dist_rel > 0.2 and abs(slope_rel) > 0.15:
            return "TENDENCIA_FORTE"
        if 20 < adx15 <= 25 and dist_rel > 0.12:
            return "TENDENCIA_FRACA"
        if adx15 < 20 and atr_expansion:
            return "LATERAL_VOLATIL"
        if adx15 < 18 and atr15 < atr15_mean20:
            return "LATERAL_COMPRESSIVA"
        return "TRANSICAO"

    def combine(self, macro: str, context15: str) -> str:
        if macro == "MACRO_TENDENCIA" and context15 == "TENDENCIA_FORTE":
            return "TENDENCIA_FORTE"
        if macro == "MACRO_LATERAL" and context15 == "LATERAL_VOLATIL":
            return "LATERAL_VOLATIL"
        if context15 == "LATERAL_COMPRESSIVA":
            return "COMPRESSAO"
        if "TRANSICAO" in macro or context15 == "TRANSICAO":
            return "TRANSICAO"
        return "TENDENCIA_FRACA"

    def classify(self, data: dict) -> RegimeSignal:
        """Classifica regime final combinando macro 60m e contexto 15m."""
        macro = self.classify_macro(
            adx60=data["adx60"],
            ema20_60=data["ema20_60"],
            ema50_60=data["ema50_60"],
            atr60=data["atr60"],
            ema20_60_prev3=data["ema20_60_prev3"],
        )

        context15 = self.classify_context15(
            adx15=data["adx15"],
            ema20_15=data["ema20"],
            ema50_15=data["ema50"],
            atr15=data["atr15"],
            ema20_15_prev3=data["ema20_15_prev3"],
            atr15_mean20=data["atr15_mean20"],
            atr15_prev=data["atr15_prev"],
        )

        regime = self.combine(macro, context15)
        direction = "NEUTRO"
        if data["ema20"] > data["ema50"]:
            direction = "COMPRA"
        elif data["ema20"] < data["ema50"]:
            direction = "VENDA"

        self._debug(
            f"Regime final | macro={macro} context15={context15} regime={regime} direction={direction}"
        )

        return RegimeSignal(
            macro=macro,
            context15=context15,
            regime=regime,
            direction=direction,
            details=f"macro={macro} context15={context15}",
        )
