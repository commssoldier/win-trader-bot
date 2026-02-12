"""Gestão de perfis operacionais."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict


@dataclass
class StrategyProfile:
    name: str
    daily_target_pct: float
    daily_stop_pct: float
    atr_multiplier: float
    max_trades_per_day: int
    adx_min: float
    risk_per_trade_pct: float
    reward_multiplier: float = 2.0
    consecutive_losses_limit: int = 3


DEFAULT_PROFILES: Dict[str, StrategyProfile] = {
    "Conservador": StrategyProfile("Conservador", 0.008, 0.006, 1.4, 4, 24.0, 0.005),
    "Moderado": StrategyProfile("Moderado", 0.012, 0.01, 1.8, 7, 20.0, 0.01),
    "Agressivo": StrategyProfile("Agressivo", 0.02, 0.015, 2.3, 12, 16.0, 0.018),
    "Personalizado": StrategyProfile("Personalizado", 0.012, 0.01, 1.8, 7, 20.0, 0.01),
}


class ProfileManager:
    """Fornece perfil selecionado com possibilidade de ajustes temporários."""

    def __init__(self) -> None:
        self._profiles = DEFAULT_PROFILES

    def get_profile(self, name: str, overrides: dict | None = None) -> StrategyProfile:
        profile = self._profiles.get(name, self._profiles["Moderado"])
        data = asdict(profile)
        if overrides:
            data.update({k: v for k, v in overrides.items() if v is not None})
        return StrategyProfile(**data)
