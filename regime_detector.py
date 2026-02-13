"""Classificação hierárquica de regime (60m + 15m) para o Sniper Adaptativo."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class RegimeSignal:
    macro: str
    context15: str
    regime: str
    direction: str
    confidence_score: float


class RegimeDetector:
    def __init__(self, debug_mode: bool = False, debug_callback: Callable[[str], None] | None = None) -> None:
        self.debug_mode = debug_mode
        self._debug_callback = debug_callback

    def _debug(self, message: str) -> None:
        if self.debug_mode and self._debug_callback:
            self._debug_callback(f"[DEBUG] {message}")

    @staticmethod
    def ema_distance_relative_atr(ema_fast: float, ema_slow: float, atr: float) -> float:
        return abs(ema_fast - ema_slow) / atr if atr > 0 else 0.0

    @staticmethod
    def ema_slope_relative_atr(ema_now: float, ema_prev_n: float, atr: float) -> float:
        return (ema_now - ema_prev_n) / atr if atr > 0 else 0.0

    def classify_macro(self, data: dict) -> str:
        adx60 = data["adx60"]
        atr60 = data["atr60"]
        dist_rel = self.ema_distance_relative_atr(data["ema20_60"], data["ema50_60"], atr60)
        slope_rel = self.ema_slope_relative_atr(data["ema20_60"], data["ema20_60_prev3"], atr60)

        self._debug(
            f"Macro60 | ADX60={adx60:.2f} ATR60={atr60:.2f} dist_rel={dist_rel:.4f} slope_rel={slope_rel:.4f}"
        )

        if adx60 > 25 and dist_rel > 0.15 and abs(slope_rel) > 0.2:
            return "MACRO_TENDENCIA"
        if adx60 < 20 or dist_rel < 0.1:
            return "MACRO_LATERAL"
        return "MACRO_TRANSICAO"

    def classify_context15(self, data: dict) -> str:
        adx15 = data["adx15"]
        atr15 = data["atr15"]
        dist_rel = self.ema_distance_relative_atr(data["ema20"], data["ema50"], atr15)
        slope_rel = self.ema_slope_relative_atr(data["ema20"], data["ema20_15_prev3"], atr15)
        atr_expansion = data["atr15"] > data["atr15_prev"] and data["atr15"] > data["atr15_mean20"]

        self._debug(
            "Ctx15 | "
            f"ADX15={adx15:.2f} ATR15={atr15:.2f} ATR15_mean20={data['atr15_mean20']:.2f} "
            f"ATR15_prev={data['atr15_prev']:.2f} dist_rel={dist_rel:.4f} slope_rel={slope_rel:.4f} "
            f"atr_expansion={atr_expansion}"
        )

        if adx15 > 25 and dist_rel > 0.2 and abs(slope_rel) > 0.15:
            return "TENDENCIA_FORTE"
        if 20 < adx15 <= 25 and dist_rel > 0.12:
            return "TENDENCIA_FRACA"
        if adx15 < 20 and atr_expansion:
            return "LATERAL"
        if adx15 < 18 and data["atr15"] < data["atr15_mean20"]:
            return "LATERAL"
        return "TRANSICAO"

    @staticmethod
    def _confidence(adx15: float, adx60: float, dist_rel_15: float, dist_rel_60: float, regime: str) -> float:
        if regime == "LATERAL":
            base = max(0.0, (20 - adx15) / 20)
            return round(min(1.0, 0.5 + base / 2), 3)
        raw = (min(adx15, 40) / 40) * 0.5 + (min(adx60, 40) / 40) * 0.3 + min(dist_rel_15 + dist_rel_60, 2.0) * 0.1
        return round(max(0.0, min(1.0, raw)), 3)

    def combine(self, macro: str, context15: str) -> str:
        if context15 == "LATERAL":
            return "LATERAL"
        if macro == "MACRO_TRANSICAO" or context15 == "TRANSICAO":
            return "TRANSICAO"
        if macro == "MACRO_TENDENCIA" and context15 == "TENDENCIA_FORTE":
            return "TENDENCIA_FORTE"
        if context15 in {"TENDENCIA_FORTE", "TENDENCIA_FRACA"}:
            return "TENDENCIA_FRACA"
        return "LATERAL"

    def classify(self, data: dict) -> RegimeSignal:
        macro = self.classify_macro(data)
        context15 = self.classify_context15(data)
        regime = self.combine(macro, context15)

        direction = "NEUTRO"
        if data["ema20"] > data["ema50"]:
            direction = "COMPRA"
        elif data["ema20"] < data["ema50"]:
            direction = "VENDA"

        dist_rel_15 = self.ema_distance_relative_atr(data["ema20"], data["ema50"], data["atr15"])
        dist_rel_60 = self.ema_distance_relative_atr(data["ema20_60"], data["ema50_60"], data["atr60"])
        confidence_score = self._confidence(data["adx15"], data["adx60"], dist_rel_15, dist_rel_60, regime)

        self._debug(
            f"Regime final | macro={macro} context15={context15} regime={regime} direction={direction} confidence={confidence_score:.3f}"
        )

        return RegimeSignal(
            macro=macro,
            context15=context15,
            regime=regime,
            direction=direction,
            confidence_score=confidence_score,
        )
