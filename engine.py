"""Motor principal de decisão e ciclo de execução."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from equity_tracker import EquityTracker
from execution_manager import ExecutionManager
from mt5_connector import MT5Connector
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


@dataclass
class SimulatedPosition:
    side: str
    entry_price: float
    stop_price: float
    take_price: float
    stop_points: float
    take_points: float
    opened_at: datetime


class TradingEngine:
    """Orquestra regras de horário, risco, regime e execução."""

    def __init__(
        self,
        logger,
        connector: MT5Connector,
        execution_manager: ExecutionManager,
        profile: StrategyProfile,
        capital: float,
        symbol: str = "WIN$",
        debug_mode: bool = False,
        debug_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.logger = logger
        self.connector = connector
        self.execution = execution_manager
        self.profile = profile
        self.symbol = symbol
        self.debug_mode = debug_mode
        self._debug_callback = debug_callback
        self.window = TradingWindow()
        self.risk = RiskManager(capital, profile)
        self.regime_detector = RegimeDetector(debug_mode=debug_mode, debug_callback=debug_callback)
        self.vol_filter = VolatilityFilter()
        self.reports = ReportGenerator()
        self.equity = EquityTracker()
        self.state = EngineState()
        self.drawdown = 0.0
        self._last_processed_candle = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self.active_position: SimulatedPosition | None = None
        self.simulated_trades_opened = 0

    def _debug(self, message: str) -> None:
        if not self.debug_mode:
            return
        payload = f"[DEBUG] {message}"
        self.logger.info(payload)
        if self._debug_callback:
            self._debug_callback(payload)

    def _signal(self, message: str) -> None:
        payload = f"[SINAL] {message}"
        self.logger.info(payload)
        if self._debug_callback:
            self._debug_callback(payload)

    def can_trade(self) -> tuple[bool, str]:
        now = now_b3()
        if is_expiration_day(now.date()):
            return True, "Dia de vencimento"
        if not is_within_trading_window(now, self.window):
            return True, "Fora do horário operacional (10:00–17:00)."
        blocked, reason = self.risk.should_block()
        if not blocked and self.simulated_trades_opened >= self.profile.max_trades_per_day:
            return True, "Máximo de trades do dia"
        return blocked, reason

    def _evaluate_open_position(self, snapshot: dict) -> None:
        """Atualiza posição simulada ativa com base no candle atual."""
        if not self.active_position:
            return

        pos = self.active_position
        hit_stop = False
        hit_take = False

        if pos.side == "BUY":
            hit_stop = snapshot["low_15m"] <= pos.stop_price
            hit_take = snapshot["high_15m"] >= pos.take_price
        else:
            hit_stop = snapshot["high_15m"] >= pos.stop_price
            hit_take = snapshot["low_15m"] <= pos.take_price

        if not hit_stop and not hit_take:
            return

        if hit_stop:
            result_points = -pos.stop_points
            self._signal("Stop atingido")
        else:
            result_points = pos.take_points
            self._signal("Alvo atingido")

        self.update_daily_result(result_points)
        self.active_position = None

    def _maybe_open_simulated_position(self, snapshot: dict) -> None:
        """Abre posição simulada se todas regras de entrada forem satisfeitas."""
        if self.active_position is not None:
            self._debug("Posição ativa em simulação; nova entrada bloqueada")
            return

        blocked, reason = self.can_trade()
        if blocked:
            self.state.blocked_reason = reason
            self._debug(f"Bloqueado por: {reason}")
            return

        current_atr = snapshot["atr15"]
        if self.vol_filter.is_extreme(snapshot["atr_series"], current_atr):
            self.state.current_regime = "PAUSADO"
            self.state.blocked_reason = "Volatilidade extrema"
            self._debug("Bloqueado por volatilidade extrema")
            return

        signal = self.regime_detector.classify(snapshot)
        self.state.current_regime = signal.regime
        self.state.blocked_reason = ""

        if signal.regime not in {"TENDENCIA_FORTE", "TENDENCIA_FRACA"}:
            return

        close_price = snapshot["close_15m"]
        adx_ok = snapshot["adx15"] > self.profile.adx_min
        volume_ok = snapshot["volume_15m"] > snapshot["volume_avg20"]

        buy_signal = (
            snapshot["ema20"] > snapshot["ema50"]
            and adx_ok
            and close_price > snapshot["breakout_high_5"]
            and volume_ok
        )
        sell_signal = (
            snapshot["ema20"] < snapshot["ema50"]
            and adx_ok
            and close_price < snapshot["breakout_low_5"]
            and volume_ok
        )

        if not buy_signal and not sell_signal:
            return

        side = "BUY" if buy_signal else "SELL"
        stop_points = self.profile.atr_multiplier * snapshot["atr15"]
        take_points = 2.0 * stop_points

        if side == "BUY":
            stop_price = close_price - stop_points
            take_price = close_price + take_points
        else:
            stop_price = close_price + stop_points
            take_price = close_price - take_points

        self.active_position = SimulatedPosition(
            side=side,
            entry_price=close_price,
            stop_price=stop_price,
            take_price=take_price,
            stop_points=stop_points,
            take_points=take_points,
            opened_at=snapshot["last_candle_time_15m"],
        )
        self.simulated_trades_opened += 1

        self._signal(
            f"{'COMPRA' if side == 'BUY' else 'VENDA'} | Entrada: {close_price:.2f} | "
            f"Stop: {stop_price:.2f} | Take: {take_price:.2f} | Risco pts: {stop_points:.2f}"
        )

    def process_market_snapshot(self, data: dict, contracts: int) -> None:
        """Processa snapshot em modo simulação de sinais (sem ordem real)."""
        _ = contracts
        if getattr(self.profile, "simulation_mode", True):
            self._evaluate_open_position(data)
            self._maybe_open_simulated_position(data)
            return

    def run_loop(
        self,
        contracts: int,
        status_callback: Callable[[str], None],
        regime_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Loop contínuo: executa somente em candle fechado de 15m."""
        self.state.running = True
        self._stop_event.clear()
        self._debug("Thread do engine iniciada")
        self._debug("Loop ativo")
        status_callback("ENGINE RODANDO")

        while not self._stop_event.is_set():
            try:
                now = now_b3()
                inside_window = is_within_trading_window(now, self.window)
                self._debug(f"Loop tick: {now.strftime('%H:%M:%S')}")
                self._debug(f"Dentro do horário: {inside_window}")
                self._debug(f"Estado da thread ativa: {self.state.running}")
                self._debug(f"Último candle 15m processado: {self._last_processed_candle}")

                connected = self.connector.ensure_connection()
                self._debug(f"Resultado conexão MT5: {connected}")
                if not connected:
                    status_callback("DESCONECTADO")
                    time.sleep(2)
                    continue

                blocked, reason = self.can_trade()
                if blocked:
                    self._debug(f"Bloqueio detectado: {reason}")
                if blocked and reason == "Fora do horário operacional (10:00–17:00).":
                    self.logger.info(reason)
                    self._debug("Fora do horário operacional")
                    status_callback("AGUARDANDO HORÁRIO")
                    time.sleep(5)
                    continue

                snapshot = self.connector.build_market_snapshot(self.symbol)
                if not snapshot:
                    time.sleep(2)
                    continue

                candle_time = snapshot["last_candle_time_15m"]
                if self._last_processed_candle == candle_time:
                    time.sleep(2)
                    continue

                self._last_processed_candle = candle_time
                self._debug(f"Novo candle 15m detectado: {candle_time.strftime('%H:%M')}")

                self.process_market_snapshot(snapshot, contracts)
                status_callback(f"Regime: {self.state.current_regime}")
                if regime_callback:
                    regime_callback(self.state.current_regime)

                result_reais = points_to_reais(self.risk.result_points)
                self._debug(f"Regime: {self.state.current_regime}")
                self._debug(
                    f"ADX15: {snapshot['adx15']:.2f} | ATR15: {snapshot['atr15']:.2f} | "
                    f"EMA20: {snapshot['ema20']:.2f} | EMA50: {snapshot['ema50']:.2f}"
                )
                self._debug(
                    f"Resultado parcial do dia: {self.risk.result_points:.2f} pts | R$ {result_reais:.2f}"
                )

                self.logger.info(
                    "Regime atual: %s | ADX: %.2f | ATR15: %.2f",
                    self.state.current_regime,
                    snapshot["adx15"],
                    snapshot["atr15"],
                )

                time.sleep(1)
            except Exception as exc:
                self.logger.exception("Erro no loop principal: %s", exc)
                self._debug(f"Erro de loop: {exc}")
                status_callback("ERRO NO LOOP")
                time.sleep(2)

        self.state.running = False
        self._debug("Evento stop recebido")
        self._debug("Thread encerrada")
        status_callback("ENGINE PARADA")

    def start(
        self,
        contracts: int,
        status_callback: Callable[[str], None],
        regime_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Inicia loop contínuo em thread separada."""
        if self.state.running:
            return
        self._debug("Thread do engine iniciada")
        self._thread = threading.Thread(
            target=self.run_loop,
            args=(contracts, status_callback, regime_callback),
            daemon=True,
            name="win-engine-loop",
        )
        self._thread.start()

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

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self.state.running = False
