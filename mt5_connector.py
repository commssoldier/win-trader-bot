"""Conector de sessão com MetaTrader5 e coleta de dados."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import MetaTrader5 as mt5
import pandas as pd


@dataclass
class ConnectionStatus:
    connected: bool
    account_type: str = "N/A"
    login: Optional[int] = None
    server: str = ""


class MT5Connector:
    """Encapsula conexão, autenticação e recuperação de dados MT5."""

    def __init__(self, logger) -> None:
        self.logger = logger
        self._status = ConnectionStatus(False)
        self._offline_since: Optional[datetime] = None
        self._offline_periods: list[tuple[datetime, datetime]] = []
        self.debug_mode = False
        self._debug_callback: Callable[[str], None] | None = None

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def offline_periods(self) -> list[tuple[datetime, datetime]]:
        return self._offline_periods

    def set_debug(self, enabled: bool, callback: Callable[[str], None] | None = None) -> None:
        """Ativa/desativa debug detalhado e callback opcional para GUI."""
        self.debug_mode = enabled
        self._debug_callback = callback
        if enabled:
            self._debug("Modo Debug do conector ativado")

    def _debug(self, message: str) -> None:
        if not self.debug_mode:
            return
        payload = f"[DEBUG] {message}"
        self.logger.info(payload)
        if self._debug_callback:
            self._debug_callback(payload)

    def connect(self, login: int, password: str, server: str) -> bool:
        try:
            self._debug("Tentando conectar no MT5")
            if not mt5.initialize(login=login, password=password, server=server):
                self.logger.error("Falha ao inicializar MT5: %s", mt5.last_error())
                self._debug(f"Falha de conexão MT5: {mt5.last_error()}")
                self._status = ConnectionStatus(False)
                return False

            account = mt5.account_info()
            if account is None:
                self.logger.error("Falha ao obter dados da conta: %s", mt5.last_error())
                self._debug(f"Falha account_info MT5: {mt5.last_error()}")
                self.disconnect()
                return False

            account_type = "Real" if getattr(account, "trade_mode", 0) == 2 else "Demo"
            self._status = ConnectionStatus(True, account_type, account.login, account.server)
            self.logger.info("Conectado ao MT5. Conta: %s | Tipo: %s", account.login, account_type)
            self._debug(f"Conectado MT5 status.connected={self._status.connected}")
            if self._offline_since:
                self._offline_periods.append((self._offline_since, datetime.now()))
                self._debug("Reconexão detectada")
                self._offline_since = None
            return True
        except Exception as exc:
            self.logger.exception("Erro inesperado na conexão MT5: %s", exc)
            self._debug(f"Exceção na conexão MT5: {exc}")
            self._status = ConnectionStatus(False)
            return False

    def disconnect(self) -> None:
        try:
            mt5.shutdown()
        finally:
            self._status = ConnectionStatus(False)
            self.logger.info("Desconectado do MT5")
            self._debug("Shutdown MT5 executado")

    def ensure_connection(self) -> bool:
        terminal = mt5.terminal_info()
        connected = bool(terminal and terminal.connected)
        self._debug(f"Verificação MT5 status.connected={connected}")
        if not connected and self._status.connected:
            self._offline_since = self._offline_since or datetime.now()
            self.logger.warning("Conexão MT5 perdida")
            self._debug("Falha de conexão detectada")
            self._status.connected = False
        elif connected and not self._status.connected:
            self._debug("Reconexão MT5 detectada")
            self._status.connected = True
        return connected

    def get_rates_dataframe(self, symbol: str, timeframe: int, bars: int = 300) -> Optional[pd.DataFrame]:
        """Busca candles e retorna DataFrame com índice temporal."""
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) < 60:
            self.logger.warning("Sem candles suficientes para %s/%s", symbol, timeframe)
            self._debug(f"Candles insuficientes em {symbol}/{timeframe}")
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        up_move = high.diff()
        down_move = -low.diff()

        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, pd.NA))
        minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, pd.NA))
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)) * 100
        return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean().fillna(0)

    def build_market_snapshot(self, symbol: str) -> Optional[dict]:
        """Monta snapshot com candles 5m/15m/60m e indicadores."""
        df5 = self.get_rates_dataframe(symbol, mt5.TIMEFRAME_M5)
        df15 = self.get_rates_dataframe(symbol, mt5.TIMEFRAME_M15)
        df60 = self.get_rates_dataframe(symbol, mt5.TIMEFRAME_H1)
        if df5 is None or df15 is None or df60 is None:
            return None

        df15 = df15.copy()
        df15["ema20"] = df15["close"].ewm(span=20, adjust=False).mean()
        df15["ema50"] = df15["close"].ewm(span=50, adjust=False).mean()
        df15["atr14"] = self._atr(df15, 14)
        df15["adx14"] = self._adx(df15, 14)

        latest = df15.iloc[-1]
        previous = df15.iloc[-2]
        rng20 = (df15["high"].tail(20).max() - df15["low"].tail(20).min()) if len(df15) >= 20 else 0.0

        return {
            "last_candle_time_15m": latest["time"],
            "atr15": float(latest["atr14"]),
            "adx15": float(latest["adx14"]),
            "ema20": float(latest["ema20"]),
            "ema50": float(latest["ema50"]),
            "atr_series": df15["atr14"].tail(80).astype(float).tolist(),
            "range20": float(rng20),
            "breakout": bool(latest["close"] > previous["high"] or latest["close"] < previous["low"]),
            "pullback": bool(df5["close"].iloc[-1] < df5["high"].tail(5).max()),
            "macro_context": "ALTA" if df60["close"].iloc[-1] > df60["close"].ewm(span=20, adjust=False).mean().iloc[-1] else "BAIXA",
        }
