"""Motor principal orientado a eventos de candle para o Sniper Adaptativo."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

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
    trailing_active: bool = False


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
        self.regime_detector = RegimeDetector(debug_mode=debug_mode, debug_callback=debug_callback)
        self.state = EngineState()

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_15m_time = None
        self._last_5m_time = None
        self._last_heartbeat_marker: tuple[int, int, int] | None = None
        self._latest_snapshot: dict | None = None
        self._latest_signal: RegimeSignal | None = None
        self._current_direction = "NEUTRO"

        self.active_position: SimulatedPosition | None = None

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
        if self.state.current_regime == "LATERAL":
            blocks.append("lateral")
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

    def _log_15m_heartbeat(self) -> None:
        now = now_b3()
        marker = (now.year, now.timetuple().tm_yday, now.hour * 60 + now.minute)
        if now.minute % 15 != 0 or self._last_heartbeat_marker == marker:
            return
        self._last_heartbeat_marker = marker

        regime = self.state.current_regime
        direction = self._current_direction
        self.logger.info(
            "[INFO] %s | Regime: %s | Direção: %s | Bloqueios: %s | Resultado: %.2f pts / R$ %.2f",
            now.strftime("%H:%M"),
            regime,
            direction,
            self._active_blocks(),
            self.risk.result_points,
            points_to_reais(self.risk.result_points),
        )

    def _evaluate_open_position(self, price: float, atr15: float) -> None:
        if not self.active_position:
            return

        pos = self.active_position
        if pos.side == "BUY":
            if price <= pos.stop_price:
                self.risk.register_trade_result(-pos.stop_points)
                self._signal("Stop atingido")
                self.active_position = None
                return
            if price >= pos.take_price:
                self.risk.register_trade_result(pos.take_points)
                self._signal("Alvo atingido")
                self.active_position = None
                return
            if not pos.trailing_active and price - pos.entry_price >= pos.stop_points:
                pos.trailing_active = True
            if pos.trailing_active:
                pos.stop_price = max(pos.stop_price, price - atr15)
        else:
            if price >= pos.stop_price:
                self.risk.register_trade_result(-pos.stop_points)
                self._signal("Stop atingido")
                self.active_position = None
                return
            if price <= pos.take_price:
                self.risk.register_trade_result(pos.take_points)
                self._signal("Alvo atingido")
                self.active_position = None
                return
            if not pos.trailing_active and pos.entry_price - price >= pos.stop_points:
                pos.trailing_active = True
            if pos.trailing_active:
                pos.stop_price = min(pos.stop_price, price + atr15)

    def _can_trade_now(self) -> bool:
        now = now_b3()
        if is_expiration_day(now.date()):
            self.state.blocked_reason = "Dia de vencimento"
            return False
        if not is_within_trading_window(now, self.window):
            self.state.blocked_reason = "Fora do horário operacional (10:00–17:00)."
            return False
        if self.state.current_regime == "LATERAL":
            self.state.blocked_reason = "Regime lateral"
            return False
        self.state.blocked_reason = ""
        return True

    def _entry_conditions_met(self, snapshot: dict, signal: RegimeSignal) -> tuple[bool, str]:
        direction = signal.direction
        if direction == "NEUTRO":
            return False, "direcao_neutra"

        pullback_ok = snapshot["pullback_to_ema"]
        breakout_buy = snapshot["close_15m"] > snapshot["breakout_high_5"]
        breakout_sell = snapshot["close_15m"] < snapshot["breakout_low_5"]
        breakout_ok = breakout_buy if direction == "COMPRA" else breakout_sell

        if signal.regime == "TENDENCIA_FORTE":
            if pullback_ok or breakout_ok:
                return True, "pullback_ou_breakout"
            return False, "sem_gatilho_tendencia_forte"

        if signal.regime == "TENDENCIA_FRACA":
            return (pullback_ok, "pullback" if pullback_ok else "sem_pullback")

        if signal.regime == "TRANSICAO":
            conditions = [
                pullback_ok,
                snapshot["rejection_5m"],
                snapshot["adx15"] > 22,
                snapshot["macro_aligned"],
                snapshot["ema_distance_atr"] > 0.5,
            ]
            return all(conditions), "filtro_transicao"

        return False, "regime_sem_trade"

    def _maybe_open_position(self, max_contracts_allowed: int) -> None:
        if self.active_position is not None or not self._latest_snapshot or not self._latest_signal:
            return
        if not self._can_trade_now():
            return

        signal = self._latest_signal
        cfg = self.risk.regime_config(signal.regime)
        if cfg.risk_percent <= 0:
            return

        levels = self.risk.build_trade_levels(self._latest_snapshot["atr15"])
        size = self.risk.calculate_position_size(self.risk.capital, cfg.risk_percent, levels.stop_points)
        contracts = min(size.contracts, max_contracts_allowed)
        if contracts <= 0:
            return

        entry_ok, reason = self._entry_conditions_met(self._latest_snapshot, signal)
        if not entry_ok:
            self._debug(f"Entrada rejeitada: {reason}")
            return

        entry_price = self._latest_snapshot["close_5m"]
        if signal.direction == "COMPRA":
            stop_price = entry_price - levels.stop_points
            take_price = entry_price + levels.take_points
            side = "BUY"
        else:
            stop_price = entry_price + levels.stop_points
            take_price = entry_price - levels.take_points
            side = "SELL"

        self.active_position = SimulatedPosition(
            side=side,
            contracts=contracts,
            entry_price=entry_price,
            stop_price=stop_price,
            take_price=take_price,
            stop_points=levels.stop_points,
            take_points=levels.take_points,
        )
        self._signal(
            f"{'COMPRA' if side == 'BUY' else 'VENDA'} | Entrada: {entry_price:.2f} | Stop: {stop_price:.2f} | "
            f"Take: {take_price:.2f} | Risco pts: {levels.stop_points:.2f} | Contratos: {contracts}"
        )

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

                self._log_15m_heartbeat()

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
                    self._last_5m_time = last_5m
                    if self._latest_snapshot:
                        self._latest_snapshot["last_candle_time_5m"] = last_5m
                        price = self._latest_snapshot["close_5m"]
                        self._evaluate_open_position(price, self._latest_snapshot["atr15"])
                    self._maybe_open_position(contracts)

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
