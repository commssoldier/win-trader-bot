"""Execução e gerenciamento de ordens no MT5."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import MetaTrader5 as mt5


@dataclass
class ExecutedTrade:
    entry_time: datetime
    exit_time: datetime | None
    side: str
    contracts: int
    entry_price: float
    exit_price: float | None
    stop_pts: float
    take_pts: float
    result_points: float
    result_reais: float
    regime: str
    exit_reason: str


class ExecutionManager:
    """Realiza ordens com SL/TP server-side e histórico interno."""

    def __init__(self, logger, symbol: str = "WIN$") -> None:
        self.logger = logger
        self.symbol = symbol
        self.trades: list[ExecutedTrade] = []

    def _point(self) -> float:
        info = mt5.symbol_info(self.symbol)
        return info.point if info else 5.0

    def send_order(
        self,
        side: str,
        volume: int,
        stop_points: float,
        take_points: float,
        order_mode: str = "market",
    ) -> bool:
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            self.logger.error("Sem tick para %s", self.symbol)
            return False

        point = self._point()
        price = tick.ask if side == "BUY" else tick.bid
        sl = price - stop_points * point if side == "BUY" else price + stop_points * point
        tp = price + take_points * point if side == "BUY" else price - take_points * point

        request = {
            "action": mt5.TRADE_ACTION_DEAL if order_mode == "market" else mt5.TRADE_ACTION_PENDING,
            "symbol": self.symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 1701,
            "comment": "win_trader_bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            self.logger.error("Falha envio ordem: %s", getattr(result, "retcode", mt5.last_error()))
            return False

        self.logger.info("Ordem enviada %s %s contratos", side, volume)
        return True
