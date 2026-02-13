"""Motor principal orientado a eventos de candle para o Sniper Adaptativo."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from equity_tracker import EquityTracker
from execution_manager import ExecutionManager
from mt5_connector import MT5Connector
from regime_detector import RegimeDetector, RegimeSignal
from risk_manager import RiskManager
from utils import TradingWindow, is_expiration_day, is_within_trading_window, now_b3, points_to_reais


@dataclass
class EngineState:
    running: bool = False
    blocked_reason: str = ""
    current_regime: str = "NEUTRO"


@dataclass
class SimulatedPosition:
    side: str
    contracts: int
    entry_price: float
    stop_price: float
    take_price: float
    stop_points: float
    take_points: float
    breakeven_armed: bool = False


class TradingEngine:
    def __init__(
        self,
        logger,
        connector: MT5Connector,
        execution_manager: ExecutionManager,
        capital: float,
        symbol: str = "WIN$",
        debug_mode: bool = False,
        debug_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.logger = logger
        self.connector = connector
        self.execution = execution_manager
        self.symbol = symbol
        self.debug_mode = debug_mode
        self._debug_callback = debug_callback

        self.window = TradingWindow()
        self.risk = RiskManager(capital)
        self.equity = EquityTracker()
        self.regime_detector = RegimeDetector(debug_mode=debug_mode, debug_callback=debug_callback)
        self.state = EngineState()

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_15m_time = None
        self._last_5m_time = None
        self._latest_snapshot: dict | None = None
        self._latest_signal: RegimeSignal | None = None
        self._current_direction = "NEUTRO"
        self._trade_count = 0

        self.active_position: SimulatedPosition | None = None

    def _debug(self, message: str) -> None:
        if not self.debug_mode:
            return
        payload = f"[DEBUG] {message}"
        self.logger.info(payload)
        if self._debug_callback:
            self._debug_callback(payload)

    def _signal(self, message: str) -> None:
        payload = f"[TRADE] {message}"
        self.logger.info(payload)
        if self._debug_callback:
            self._debug_callback(payload)

    def _log_startup(self, contracts_limit: int) -> None:
        status = "OK" if self.connector.status.connected else "DESCONECTADO"
        self.logger.info(
            "[STARTUP] %s | simbolo=%s | estrategia=Sniper Adaptativo | capital=%.2f | max_contratos=%s | "
            "timeframes=5m,15m,60m | mt5=%s | debug=%s",
            now_b3().strftime("%Y-%m-%d %H:%M:%S"),
            self.symbol,
            self.risk.capital,
            contracts_limit,
            status,
            "on" if self.debug_mode else "off",
        )

    def _active_blocks(self) -> str:
        blocks = []
        now = now_b3()
        if is_expiration_day(now.date()):
            blocks.append("vencimento")
        if not is_within_trading_window(now, self.window):
            blocks.append("fora_horario")
        if self._latest_signal and self._latest_signal.regime != "TENDENCIA_FORTE":
            blocks.append("regime_nao_qualificado")
        return ",".join(blocks) if blocks else "nenhum"

    def _log_15m_event(self, tag: str) -> None:
        if not self._latest_snapshot or not self._latest_signal:
            return
        snap = self._latest_snapshot
        sig = self._latest_signal
        self.logger.info(
            f"[{tag}] %s | macro=%s | contexto15=%s | regime=%s | direcao=%s | conf=%.3f | "
            "ADX15=%.2f ADX60=%.2f ATR15=%.2f ATR60=%.2f | bloqueios=%s | resultado=%.2f pts / R$ %.2f",
            snap["last_candle_time_15m"].strftime("%Y-%m-%d %H:%M"),
            sig.macro,
            sig.context15,
            sig.regime,
            sig.direction,
            sig.confidence_score,
            snap["adx15"],
            snap["adx60"],
            snap["atr15"],
            snap["atr60"],
            self._active_blocks(),
            self.risk.result_points,
            points_to_reais(self.risk.result_points),
        )

    def _can_trade_now(self) -> bool:
        now = now_b3()
        if is_expiration_day(now.date()):
            self.state.blocked_reason = "Dia de vencimento"
            return False
        if not is_within_trading_window(now, self.window):
            self.state.blocked_reason = "Fora do horário operacional (10:00–17:00)."
            return False
        self.state.blocked_reason = ""
        return True

    def _entry_conditions_met(self, snapshot: dict, signal: RegimeSignal) -> tuple[bool, str]:
        if signal.macro != "MACRO_TENDENCIA":
            return False, "macro"
        if signal.context15 != "TENDENCIA_FORTE":
            return False, "context15"
        if signal.confidence_score < 0.75:
            return False, "confidence"

        close_15 = snapshot["close_15m"]
        high_15 = snapshot["high_15m"]
        low_15 = snapshot["low_15m"]
        ema20 = snapshot["ema20_15"]
        ema50 = snapshot["ema50_15"]

        if signal.direction == "COMPRA":
            pullback_ok = ema20 > ema50 and low_15 <= ema20 and close_15 > ema20
        elif signal.direction == "VENDA":
            pullback_ok = ema20 < ema50 and high_15 >= ema20 and close_15 < ema20
        else:
            pullback_ok = False

        return pullback_ok, "pullback"

    def _open_position(self, contracts: int, snapshot: dict, signal: RegimeSignal) -> None:
        stop_points_base = 1.2 * snapshot["atr15"]
        dist_mult = 2.5 if snapshot["ema_distance_atr"] > 1.5 else 2.0
        take_points = dist_mult * stop_points_base
        entry_price = snapshot["close_5m"]

        if signal.direction == "COMPRA":
            stop_price = min(entry_price - stop_points_base, snapshot["low_15m"] - 1.0)
            side = "BUY"
            stop_points = entry_price - stop_price
            take_price = entry_price + take_points
        else:
            stop_price = max(entry_price + stop_points_base, snapshot["high_15m"] + 1.0)
            side = "SELL"
            stop_points = stop_price - entry_price
            take_price = entry_price - take_points

        self.active_position = SimulatedPosition(
            side=side,
            contracts=contracts,
            entry_price=entry_price,
            stop_price=stop_price,
            take_price=take_price,
            stop_points=stop_points,
            take_points=take_points,
        )
        self._signal(
            f"ABERTURA {('COMPRA' if side == 'BUY' else 'VENDA')} | Entrada={entry_price:.2f} | "
            f"SL={stop_price:.2f} | TP={take_price:.2f} | R={stop_points:.2f}"
        )

    def _close_position(self, result_points: float, reason: str) -> None:
        self.risk.register_trade_result(result_points)
        self._trade_count += 1
        total_reais = points_to_reais(self.risk.result_points)
        self.equity.add(total_reais, self._trade_count, total_reais)
        self._signal(f"FECHAMENTO {reason} | Resultado={result_points:.2f} pts | Acumulado={self.risk.result_points:.2f} pts")
        self.active_position = None

    def _manage_open_position(self, snapshot: dict) -> None:
        if not self.active_position:
            return

        pos = self.active_position
        price = snapshot["close_5m"]

        if pos.side == "BUY":
            if price <= pos.stop_price:
                self._close_position(-pos.stop_points, "STOP")
                return
            if price >= pos.take_price:
                self._close_position(pos.take_points, "TAKE")
                return
            if not pos.breakeven_armed and price - pos.entry_price >= pos.stop_points:
                pos.stop_price = pos.entry_price
                pos.breakeven_armed = True
            if pos.breakeven_armed:
                pos.stop_price = max(pos.stop_price, snapshot["ema20_5"])
        else:
            if price >= pos.stop_price:
                self._close_position(-pos.stop_points, "STOP")
                return
            if price <= pos.take_price:
                self._close_position(pos.take_points, "TAKE")
                return
            if not pos.breakeven_armed and pos.entry_price - price >= pos.stop_points:
                pos.stop_price = pos.entry_price
                pos.breakeven_armed = True
            if pos.breakeven_armed:
                pos.stop_price = min(pos.stop_price, snapshot["ema20_5"])

    def _maybe_open_position(self, max_contracts_allowed: int) -> None:
        if self.active_position is not None or not self._latest_snapshot or not self._latest_signal:
            return
        if not self._can_trade_now():
            return

        qualified, _ = self._entry_conditions_met(self._latest_snapshot, self._latest_signal)
        if not qualified:
            return

        stop_points = 1.2 * self._latest_snapshot["atr15"]
        size = self.risk.calculate_position_size(self.risk.capital, 0.0075, stop_points)
        contracts = min(size.contracts, max_contracts_allowed)
        if contracts <= 0:
            return

        self._open_position(contracts, self._latest_snapshot, self._latest_signal)

    def _process_15m_event(self, contracts: int, tag: str) -> None:
        snapshot = self.connector.build_market_snapshot(self.symbol)
        if not snapshot:
            return
        self._latest_snapshot = snapshot
        self._latest_signal = self.regime_detector.classify(snapshot)
        self.state.current_regime = self._latest_signal.regime
        self._current_direction = self._latest_signal.direction
        self._last_15m_time = snapshot["last_candle_time_15m"]

        self._log_15m_event(tag)
        self._maybe_open_position(contracts)

    def _process_5m_event(self, contracts: int) -> None:
        snapshot = self.connector.build_market_snapshot(self.symbol)
        if not snapshot:
            return
        self._latest_snapshot = snapshot
        self._last_5m_time = snapshot["last_candle_time_5m"]
        self._manage_open_position(snapshot)
        self._maybe_open_position(contracts)

    def run_loop(self, contracts: int, status_callback: Callable[[str], None], regime_callback: Callable[[str], None] | None = None) -> None:
        self.state.running = True
        self._stop_event.clear()
        status_callback("ENGINE RODANDO")

        self._process_15m_event(contracts, tag="INIT")
        if regime_callback:
            regime_callback(self.state.current_regime)

        while not self._stop_event.is_set():
            try:
                if not self.connector.ensure_connection():
                    status_callback("DESCONECTADO")
                    time.sleep(1)
                    continue

                if not self._can_trade_now() and self.state.blocked_reason.startswith("Fora do horário"):
                    status_callback("AGUARDANDO HORÁRIO")

                last_15m = self.connector.get_last_candle_time_15m(self.symbol)
                if last_15m is not None and last_15m != self._last_15m_time:
                    self._process_15m_event(contracts, tag="CANDLE15")
                    status_callback(f"Regime: {self.state.current_regime}")
                    if regime_callback:
                        regime_callback(self.state.current_regime)

                last_5m = self.connector.get_last_candle_time_5m(self.symbol)
                if last_5m is not None and last_5m != self._last_5m_time:
                    self._process_5m_event(contracts)

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

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self.state.running = False
