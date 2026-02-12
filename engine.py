"""Motor principal de decisão e ciclo de execução."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from equity_tracker import EquityTracker
from execution_manager import ExecutionManager
from profile_manager import StrategyProfile
from regime_detector import RegimeDetector
from report_generator import ReportGenerator
from risk_manager import RiskManager
from utils import TradingWindow, is_expiration_day, is_within_trading_window, now_b3, points_to_reais
from volatility_filter import VolatilityFilter


@dataclass
class EngineState:
    running: bool = False
    blocked_reason: str = ""
    current_regime: str = "NEUTRO"


class TradingEngine:
    """Orquestra regras de horário, risco, regime e execução."""

    def __init__(
        self,
        logger,
        execution_manager: ExecutionManager,
        profile: StrategyProfile,
        capital: float,
    ) -> None:
        self.logger = logger
        self.execution = execution_manager
        self.profile = profile
        self.window = TradingWindow()
        self.risk = RiskManager(capital, profile)
        self.regime_detector = RegimeDetector()
        self.vol_filter = VolatilityFilter()
        self.reports = ReportGenerator()
        self.equity = EquityTracker()
        self.state = EngineState()
        self.drawdown = 0.0

    def can_trade(self) -> tuple[bool, str]:
        now = now_b3()
        if is_expiration_day(now.date()):
            return False, "Dia de vencimento"
        if not is_within_trading_window(now, self.window):
            return False, "Fora do horário de operação"
        return self.risk.should_block()

    def process_market_snapshot(self, data: dict) -> None:
        blocked, reason = self.can_trade()
        if blocked:
            self.state.blocked_reason = reason
            return

        current_atr = data["atr15"]
        if self.vol_filter.is_extreme(data["atr_series"], current_atr):
            self.state.current_regime = "PAUSADO"
            return

        signal = self.regime_detector.classify(
            adx15=data["adx15"],
            ema20=data["ema20"],
            ema50=data["ema50"],
            limit_trend=self.profile.adx_min,
            limit_range=18.0,
            range20=data["range20"],
            vol_extreme=False,
        )
        self.state.current_regime = signal.regime

        if signal.regime == "TENDENCIA":
            stop_pts, take_pts = self.risk.compute_stop_take_points(current_atr)
            side = "BUY" if signal.direction == "COMPRA" else "SELL"
            mode = "market" if data.get("breakout", True) else "limit"
            self.execution.send_order(side, data["contracts"], stop_pts, take_pts, mode)

    def update_daily_result(self, result_points: float) -> None:
        self.risk.register_trade_result(result_points)
        result_reais = points_to_reais(self.risk.result_points)
        self.equity.add(result_reais, self.risk.trade_count, result_reais)
        self.drawdown = min(self.drawdown, result_reais)

    def maybe_expand_target(self, expansion_enabled: bool, before_13h: bool) -> None:
        if not expansion_enabled or not before_13h:
            return
        limits = self.risk.limits()
        result_reais = points_to_reais(self.risk.result_points)
        if result_reais >= limits.daily_target_reais and not self.risk.expansion_applied:
            self.risk.apply_expansion()
            self.logger.info("Expansão de meta ativada")

    def close_day(self, profile_name: str, capital: float, offline_minutes: float, offline_count: int) -> None:
        total_reais = points_to_reais(self.risk.result_points)
        limits = self.risk.limits()
        self.reports.generate_daily_report(
            report_date=date.today(),
            profile_name=profile_name,
            capital=capital,
            result_reais=total_reais,
            result_points=self.risk.result_points,
            trades=self.execution.trades,
            meta_hit=total_reais >= limits.daily_target_reais,
            stop_hit=total_reais <= -limits.daily_stop_reais,
            drawdown=abs(self.drawdown),
            offline_count=offline_count,
            offline_duration_min=offline_minutes,
        )
        base_name = date.today().isoformat()
        self.equity.export_csv(self.reports.base / f"{base_name}_equity.csv")
        self.equity.export_monthly_stats(self.reports.base / f"{base_name}_monthly_stats.csv")

    def start(self, on_tick: Callable[[], None]) -> None:
        self.state.running = True
        try:
            on_tick()
        except Exception as exc:
            self.logger.exception("Erro no loop principal: %s", exc)
            self.state.running = False

    def stop(self) -> None:
        self.state.running = False
