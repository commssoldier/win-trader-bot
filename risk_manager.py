"""Gestão de risco do modelo Sniper Adaptativo."""
from __future__ import annotations

from dataclasses import dataclass

from utils import WIN_POINT_VALUE, max_contracts


@dataclass(frozen=True)
class RegimeRiskConfig:
    risk_percent: float
    allow_pullback: bool
    allow_breakout: bool


REGIME_RISK_MAP = {
    "TENDENCIA_FORTE": RegimeRiskConfig(risk_percent=0.0075, allow_pullback=True, allow_breakout=True),
    "TENDENCIA_FRACA": RegimeRiskConfig(risk_percent=0.0050, allow_pullback=True, allow_breakout=False),
    "TRANSICAO": RegimeRiskConfig(risk_percent=0.0035, allow_pullback=True, allow_breakout=False),
    "LATERAL": RegimeRiskConfig(risk_percent=0.0, allow_pullback=False, allow_breakout=False),
}


@dataclass
class TradeLevels:
    stop_points: float
    take_points: float
    trailing_trigger_points: float
    trailing_distance_points: float


@dataclass
class PositionSizeResult:
    contracts: int
    risk_amount: float


class RiskManager:
    """Calcula tamanho de posição e níveis técnicos da estratégia única."""

    def __init__(self, capital: float) -> None:
        self.capital = capital
        self.result_points = 0.0

    def register_trade_result(self, result_points: float) -> None:
        self.result_points += result_points

    @staticmethod
    def calculate_position_size(balance: float, risk_percent: float, stop_points: float) -> PositionSizeResult:
        """Retorna contratos pelo risco financeiro e stop em pontos."""
        if balance <= 0 or risk_percent <= 0 or stop_points <= 0:
            return PositionSizeResult(contracts=0, risk_amount=0.0)

        risk_amount = balance * risk_percent
        risk_per_contract = stop_points * WIN_POINT_VALUE
        if risk_per_contract <= 0:
            return PositionSizeResult(contracts=0, risk_amount=risk_amount)

        contracts = int(risk_amount // risk_per_contract)
        contracts = max(0, min(contracts, max_contracts(balance)))
        return PositionSizeResult(contracts=contracts, risk_amount=risk_amount)

    @staticmethod
    def build_trade_levels(atr15: float) -> TradeLevels:
        stop_points = 1.2 * atr15
        take_points = 2.0 * stop_points
        return TradeLevels(
            stop_points=stop_points,
            take_points=take_points,
            trailing_trigger_points=stop_points,
            trailing_distance_points=atr15,
        )

    def regime_config(self, regime: str) -> RegimeRiskConfig:
        return REGIME_RISK_MAP.get(regime, REGIME_RISK_MAP["LATERAL"])
