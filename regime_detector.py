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
    structure15: str
    structure60: str
    pivot_count15: int
    pivot_count60: int
    dist_rel_15: float


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

    @staticmethod
    def _detect_fractal_pivots(highs: list[float], lows: list[float], lookback: int) -> tuple[list[float], list[float]]:
        """Detecta pivôs fractais simples (2 candles antes/depois) no recorte de lookback."""
        if not highs or not lows:
            return [], []

        start = max(2, len(highs) - lookback)
        end = len(highs) - 2
        pivot_highs: list[float] = []
        pivot_lows: list[float] = []

        for i in range(start, end):
            is_pivot_high = (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i + 2]
            )
            is_pivot_low = (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i + 2]
            )

            if is_pivot_high:
                pivot_highs.append(highs[i])
            if is_pivot_low:
                pivot_lows.append(lows[i])

        return pivot_highs[-3:], pivot_lows[-3:]

    @staticmethod
    def _structure_from_pivots(pivot_highs: list[float], pivot_lows: list[float]) -> str:
        if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
            if pivot_highs[-1] > pivot_highs[-2] and pivot_lows[-1] > pivot_lows[-2]:
                return "HH_HL"
            if pivot_highs[-1] < pivot_highs[-2] and pivot_lows[-1] < pivot_lows[-2]:
                return "LH_LL"
        return "NEUTRA"

    def classify_macro(self, data: dict) -> tuple[str, str, int]:
        adx60 = data["adx60"]
        atr60 = data["atr60"]
        dist_rel = self.ema_distance_relative_atr(data["ema20_60"], data["ema50_60"], atr60)

        pivot_highs, pivot_lows = self._detect_fractal_pivots(
            data["high_series_60"],
            data["low_series_60"],
            lookback=8,
        )
        structure60 = self._structure_from_pivots(pivot_highs, pivot_lows)
        pivot_count60 = len(pivot_highs) + len(pivot_lows)
        ema_aligned_60 = (data["ema20_60"] > data["ema50_60"]) or (data["ema20_60"] < data["ema50_60"])

        self._debug(
            f"Macro60 | ADX60={adx60:.2f} ATR60={atr60:.2f} dist_rel={dist_rel:.4f} "
            f"structure={structure60} pivots={pivot_count60}"
        )

        if adx60 > 20 and structure60 in {"HH_HL", "LH_LL"} and ema_aligned_60:
            return "MACRO_TENDENCIA", structure60, pivot_count60
        if adx60 < 20 or structure60 == "NEUTRA":
            return "MACRO_LATERAL", structure60, pivot_count60
        return "MACRO_TRANSICAO", structure60, pivot_count60

    def classify_context15(self, data: dict) -> tuple[str, bool, str, int, float]:
        adx15 = data["adx15"]
        atr15 = data["atr15"]
        dist_rel = self.ema_distance_relative_atr(data["ema20"], data["ema50"], atr15)
        slope_rel = self.ema_slope_relative_atr(data["ema20"], data["ema20_15_prev3"], atr15)
        atr_expansion = data["atr15"] > (data["atr15_mean30"] * 1.10)

        pivot_highs, pivot_lows = self._detect_fractal_pivots(
            data["high_series_15"],
            data["low_series_15"],
            lookback=12,
        )
        structure15 = self._structure_from_pivots(pivot_highs, pivot_lows)
        pivot_count15 = len(pivot_highs) + len(pivot_lows)

        self._debug(
            "Ctx15 | "
            f"ADX15={adx15:.2f} ATR15={atr15:.2f} ATR15_mean30={data['atr15_mean30']:.2f} "
            f"dist_rel={dist_rel:.4f} slope_rel={slope_rel:.4f} atr_expansion={atr_expansion} "
            f"structure={structure15} pivots={pivot_count15}"
        )

        if structure15 in {"HH_HL", "LH_LL"} and adx15 > 25 and dist_rel > 0.2 and abs(slope_rel) > 0.15:
            return "TENDENCIA_FORTE", atr_expansion, structure15, pivot_count15, dist_rel
        if structure15 in {"HH_HL", "LH_LL"} and 20 < adx15 <= 25 and dist_rel > 0.12:
            return "TENDENCIA_FRACA", atr_expansion, structure15, pivot_count15, dist_rel
        if adx15 < 20 and atr_expansion:
            return "LATERAL", atr_expansion, structure15, pivot_count15, dist_rel
        if adx15 < 18 and data["atr15"] < data["atr15_mean30"]:
            return "LATERAL", atr_expansion, structure15, pivot_count15, dist_rel
        return "TRANSICAO", atr_expansion, structure15, pivot_count15, dist_rel

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
        macro, structure60, pivot_count60 = self.classify_macro(data)
        context15, atr_expansion, structure15, pivot_count15, dist_rel_15 = self.classify_context15(data)
        regime = self.combine(macro, context15)

        direction = "NEUTRO"
        if data["ema20"] > data["ema50"]:
            direction = "COMPRA"
        elif data["ema20"] < data["ema50"]:
            direction = "VENDA"

        dist_rel_60 = self.ema_distance_relative_atr(data["ema20_60"], data["ema50_60"], data["atr60"])
        confidence_score = self._confidence(data["adx15"], data["adx60"], dist_rel_15, dist_rel_60, regime)

        if 20 <= data["adx15"] <= 25:
            confidence_score = round(confidence_score * 0.9, 3)
            if not atr_expansion:
                regime = "TRANSICAO"

        self._debug(
            f"Regime final | macro={macro} context15={context15} regime={regime} direction={direction} "
            f"confidence={confidence_score:.3f} structure15={structure15} structure60={structure60}"
        )

        return RegimeSignal(
            macro=macro,
            context15=context15,
            regime=regime,
            direction=direction,
            confidence_score=confidence_score,
            structure15=structure15,
            structure60=structure60,
            pivot_count15=pivot_count15,
            pivot_count60=pivot_count60,
            dist_rel_15=dist_rel_15,
        )
