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
        self._last_signal = None
        self._last_5m_log_marker: tuple[int, int] | None = None
        self._current_direction = "NEUTRO"

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

    def _log_startup(self, contracts: int) -> None:
        status = "OK" if self.connector.status.connected else "DESCONECTADO"
        self.logger.info(
            "[STARTUP] %s | simbolo=%s | perfil=%s | capital=%.2f | contratos=%s | "
            "timeframes=5m,15m,60m | mt5=%s | debug=%s",
            now_b3().strftime("%Y-%m-%d %H:%M:%S"),
            self.symbol,
            self.profile.name,
            self.risk.capital,
            contracts,
            status,
            "on" if self.debug_mode else "off",
        )

    def _log_15m_event(self, snapshot: dict, tag: str = "CANDLE15") -> None:
        signal = self._last_signal
        blocked, reason = self.can_trade()
        bloqueio = reason if blocked else "Nenhum"
        result_reais = points_to_reais(self.risk.result_points)

        if signal is None:
            self.logger.info(
                f"[{tag}] %s | regime=INDISPONIVEL | direction=%s | ADX15=%.2f ADX60=%.2f "
                "ATR15=%.2f ATR60=%.2f | bloqueios=%s | resultado=%.2f pts / R$ %.2f",
                snapshot["last_candle_time_15m"].strftime("%Y-%m-%d %H:%M"),
                self._current_direction,
                snapshot["adx15"],
                snapshot["adx60"],
                snapshot["atr15"],
                snapshot["atr60"],
                bloqueio,
                self.risk.result_points,
                result_reais,
            )
            return

        resumo = f"Macro {signal.macro}, contexto {signal.context15}"
        self.logger.info(
            f"[{tag}] %s | macro=%s | contexto15=%s | regime=%s | direction=%s | "
            "ADX15=%.2f ADX60=%.2f ATR15=%.2f ATR60=%.2f EMA20=%.2f EMA50=%.2f | "
            "bloqueios=%s | resultado=%.2f pts / R$ %.2f | %s",
            snapshot["last_candle_time_15m"].strftime("%Y-%m-%d %H:%M"),
            signal.macro,
            signal.context15,
            signal.regime,
            signal.direction,
            snapshot["adx15"],
            snapshot["adx60"],
            snapshot["atr15"],
            snapshot["atr60"],
            snapshot["ema20"],
            snapshot["ema50"],
            bloqueio,
            self.risk.result_points,
            result_reais,
            resumo,
        )

    def _log_5m_heartbeat(self) -> None:
        now = now_b3()
        marker = (now.hour, now.minute)
        if now.minute % 5 != 0 or self._last_5m_log_marker == marker:
            return
        self._last_5m_log_marker = marker

        blocked, reason = self.can_trade()
        bloqueio = reason if blocked else "Nenhum"
        result_reais = points_to_reais(self.risk.result_points)
        self.logger.info(
            "[INFO] %s | Regime: %s | Direção: %s | Bloqueio: %s | Resultado: %.2f pts / R$ %.2f",
            now.strftime("%H:%M"),
            self.state.current_regime,
            self._current_direction,
            bloqueio,
            self.risk.result_points,
            result_reais,
        )

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
        if not self.active_position:
            return
        pos = self.active_position
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
        if self.active_position is not None:
            return

        signal = self.regime_detector.classify(snapshot)
        self._last_signal = signal
        self._current_direction = signal.direction
        self.state.current_regime = signal.regime
        self.state.blocked_reason = ""

        blocked, reason = self.can_trade()
        if blocked:
            self.state.blocked_reason = reason
            return

        current_atr = snapshot["atr15"]
        if self.vol_filter.is_extreme(snapshot["atr_series"], current_atr):
            self.state.current_regime = "PAUSADO"
            self.state.blocked_reason = "Volatilidade extrema"
            return

        if signal.regime not in {"TENDENCIA_FORTE", "TENDENCIA_FRACA"}:
            return

        close_price = snapshot["close_15m"]
        adx_ok = snapshot["adx15"] > self.profile.adx_min
        volume_ok = snapshot["volume_15m"] > snapshot["volume_avg20"]
        buy_signal = snapshot["ema20"] > snapshot["ema50"] and adx_ok and close_price > snapshot["breakout_high_5"] and volume_ok
        sell_signal = snapshot["ema20"] < snapshot["ema50"] and adx_ok and close_price < snapshot["breakout_low_5"] and volume_ok
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

        self.active_position = SimulatedPosition(side, close_price, stop_price, take_price, stop_points, take_points, snapshot["last_candle_time_15m"])
        self.simulated_trades_opened += 1
        self._signal(
            f"{'COMPRA' if side == 'BUY' else 'VENDA'} | Entrada: {close_price:.2f} | "
            f"Stop: {stop_price:.2f} | Take: {take_price:.2f} | Risco pts: {stop_points:.2f}"
        )

    def process_market_snapshot(self, data: dict, contracts: int) -> None:
        _ = contracts
        if getattr(self.profile, "simulation_mode", True):
            self._evaluate_open_position(data)
            self._maybe_open_simulated_position(data)

    def _process_snapshot_event(self, contracts: int, snapshot: dict, tag: str) -> None:
        self._last_processed_candle = snapshot["last_candle_time_15m"]
        self.process_market_snapshot(snapshot, contracts)
        self._log_15m_event(snapshot, tag=tag)

    def run_loop(self, contracts: int, status_callback: Callable[[str], None], regime_callback: Callable[[str], None] | None = None) -> None:
        self.state.running = True
        self._stop_event.clear()
        status_callback("ENGINE RODANDO")

        # Snapshot inicial (evento de start)
        initial_snapshot = self.connector.build_market_snapshot(self.symbol)
        if initial_snapshot:
            self._process_snapshot_event(contracts, initial_snapshot, tag="INIT")
            status_callback(f"Regime: {self.state.current_regime}")
            if regime_callback:
                regime_callback(self.state.current_regime)

        while not self._stop_event.is_set():
            try:
                connected = self.connector.ensure_connection()
                if not connected:
                    status_callback("DESCONECTADO")
                    self._log_5m_heartbeat()
                    time.sleep(1)
                    continue

                self._log_5m_heartbeat()

                blocked, reason = self.can_trade()
                if blocked and reason == "Fora do horário operacional (10:00–17:00).":
                    status_callback("AGUARDANDO HORÁRIO")
                    time.sleep(1)
                    continue

                last_15m = self.connector.get_last_candle_time_15m(self.symbol)
                if last_15m is None or self._last_processed_candle == last_15m:
                    time.sleep(1)
                    continue

                snapshot = self.connector.build_market_snapshot(self.symbol)
                if not snapshot:
                    time.sleep(1)
                    continue

                # garante processamento apenas em evento de novo candle 15m
                if self._last_processed_candle == snapshot["last_candle_time_15m"]:
                    time.sleep(1)
                    continue

                self._process_snapshot_event(contracts, snapshot, tag="CANDLE15")
                status_callback(f"Regime: {self.state.current_regime}")
                if regime_callback:
                    regime_callback(self.state.current_regime)

                time.sleep(1)
            except Exception as exc:
                self.logger.exception("Erro no loop principal: %s", exc)
                status_callback("ERRO NO LOOP")
                time.sleep(1)

        self.state.running = False
        status_callback("ENGINE PARADA")

    def start(self, contracts: int, status_callback: Callable[[str], None], regime_callback: Callable[[str], None] | None = None) -> None:
        if self.state.running:
            return
        self._thread = threading.Thread(target=self.run_loop, args=(contracts, status_callback, regime_callback), daemon=True, name="win-engine-loop")
        self._thread.start()
        self._log_startup(contracts)

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
