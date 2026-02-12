"""Gestão matemática de risco e limites diários."""
from __future__ import annotations

from dataclasses import dataclass

from profile_manager import StrategyProfile
from utils import max_contracts, points_to_reais, reais_to_points


@dataclass
class RiskLimits:
    max_contracts: int
    daily_target_reais: float
    daily_stop_reais: float
    daily_target_points: float
    daily_stop_points: float


class RiskManager:
    """Calcula limites e valida bloqueios diários."""

    def __init__(self, capital: float, profile: StrategyProfile) -> None:
        self.capital = capital
        self.profile = profile
        self.result_points = 0.0
        self.consecutive_losses = 0
        self.trade_count = 0
        self.expansion_enabled = False
        self.expansion_applied = False
        self.expansion_trades_left = 0

    def limits(self) -> RiskLimits:
        target_reais = self.capital * self.profile.daily_target_pct
        stop_reais = self.capital * self.profile.daily_stop_pct
        return RiskLimits(
            max_contracts=max_contracts(self.capital),
            daily_target_reais=target_reais,
            daily_stop_reais=stop_reais,
            daily_target_points=reais_to_points(target_reais),
            daily_stop_points=reais_to_points(stop_reais),
        )

    def risk_trade_points(self) -> float:
        return reais_to_points(self.capital * self.profile.risk_per_trade_pct)

    def compute_stop_take_points(self, atr15: float) -> tuple[float, float]:
        stop_atr = atr15 * self.profile.atr_multiplier
        stop_final = min(stop_atr, self.risk_trade_points())
        take = stop_final * self.profile.reward_multiplier
        return stop_final, take

    def register_trade_result(self, result_points: float) -> None:
        self.result_points += result_points
        self.trade_count += 1
        result_reais = points_to_reais(result_points)
        self.consecutive_losses = self.consecutive_losses + 1 if result_reais < 0 else 0
        if self.expansion_applied and self.expansion_trades_left > 0:
            self.expansion_trades_left -= 1
            if result_reais < 0 and self.expansion_trades_left == 1:
                self.expansion_trades_left = 0

    def should_block(self) -> tuple[bool, str]:
        limits = self.limits()
        result_reais = points_to_reais(self.result_points)
        if result_reais >= limits.daily_target_reais and not self.expansion_applied:
            return True, "Meta diária atingida"
        if result_reais <= -limits.daily_stop_reais:
            return True, "Stop diário atingido"
        if self.consecutive_losses >= self.profile.consecutive_losses_limit:
            return True, "Limite de perdas consecutivas"
        if self.trade_count >= self.profile.max_trades_per_day + self.expansion_trades_left:
            return True, "Máximo de trades do dia"
        return False, ""

    def apply_expansion(self) -> None:
        self.expansion_applied = True
        self.expansion_trades_left = 2
