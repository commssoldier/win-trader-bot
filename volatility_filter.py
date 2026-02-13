"""Filtro de volatilidade extrema."""
from __future__ import annotations

import numpy as np


class VolatilityFilter:
    """Determina se há volatilidade extrema pelo ATR."""

    def is_extreme(self, atr_series: list[float], current_atr: float) -> bool:
        if len(atr_series) < 20:
            return False
        arr = np.array(atr_series[-50:], dtype=float)
        threshold = arr.mean() + 2 * arr.std(ddof=0)
        return current_atr > threshold
