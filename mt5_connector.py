"""Conector de sessão MT5 e construção de snapshots de mercado."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import MetaTrader5 as mt5
import numpy as np
import pandas as pd


@dataclass
class ConnectionStatus:
    connected: bool
    account_type: str = "N/A"
    login: Optional[int] = None
    server: str = ""


class MT5Connector:
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
                self._status = ConnectionStatus(False)
                self.logger.error("Falha ao inicializar MT5: %s", mt5.last_error())
                return False

            account = mt5.account_info()
            if account is None:
                self.disconnect()
                self.logger.error("Falha ao obter dados da conta: %s", mt5.last_error())
                return False

            account_type = "Real" if getattr(account, "trade_mode", 0) == 2 else "Demo"
            self._status = ConnectionStatus(True, account_type, account.login, account.server)
            self.logger.info("Conectado ao MT5. Conta: %s | Tipo: %s", account.login, account_type)
            self._debug(f"Conectado MT5 status.connected={self._status.connected}")
            if self._offline_since:
                self._offline_periods.append((self._offline_since, datetime.now()))
                self._offline_since = None
            return True
        except Exception as exc:
            self.logger.exception("Erro inesperado na conexão MT5: %s", exc)
            self._status = ConnectionStatus(False)
            return False

    def disconnect(self) -> None:
        try:
            mt5.shutdown()
        finally:
            self._status = ConnectionStatus(False)
            self.logger.info("Desconectado do MT5")

    def ensure_connection(self) -> bool:
        terminal = mt5.terminal_info()
        connected = bool(terminal and terminal.connected)
        if not connected and self._status.connected:
            self._offline_since = self._offline_since or datetime.now()
            self.logger.warning("Conexão MT5 perdida")
            self._status.connected = False
        elif connected and not self._status.connected:
            self._status.connected = True
            self.logger.info("Reconexão MT5 detectada")
            if self._offline_since:
                self._offline_periods.append((self._offline_since, datetime.now()))
                self._offline_since = None
        return connected

    def _last_candle_time(self, symbol: str, timeframe: int) -> Optional[pd.Timestamp]:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 1)
        if rates is None or len(rates) == 0:
            return None
        return pd.to_datetime(rates[0]["time"], unit="s")

    def get_last_candle_time_15m(self, symbol: str) -> Optional[pd.Timestamp]:
        return self._last_candle_time(symbol, mt5.TIMEFRAME_M15)

    def get_last_candle_time_5m(self, symbol: str) -> Optional[pd.Timestamp]:
        return self._last_candle_time(symbol, mt5.TIMEFRAME_M5)

    def get_rates_dataframe(self, symbol: str, timeframe: int, bars: int = 300) -> Optional[pd.DataFrame]:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) < 60:
            self._debug(f"Candles insuficientes em {symbol}/{timeframe}")
            return None

        df = pd.DataFrame(rates)
        if df.empty:
            return None
        df["time"] = pd.to_datetime(df["time"], unit="s")

        numeric_cols = ["open", "high", "low", "close", "tick_volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

        df = df.dropna(subset=numeric_cols).copy()
        if len(df) < 60:
            self._debug(f"Dados inválidos após normalização em {symbol}/{timeframe}")
            return None

        if self.debug_mode:
            self._debug(f"{symbol}/{timeframe} dtypes: {df.dtypes.to_dict()}")
            self._debug(f"{symbol}/{timeframe} shape: {df.shape}")
        return df

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift()).abs()
        tr3 = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean().fillna(0).astype("float64")

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"].astype("float64")
        low = df["low"].astype("float64")
        close = df["close"].astype("float64")

        up_move = high.diff()
        down_move = -low.diff()

        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, np.nan), index=df.index, dtype="float64")
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, np.nan), index=df.index, dtype="float64")

        tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1).astype("float64")
        atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, np.nan).astype("float64")

        plus_di = (100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr).astype("float64")
        minus_di = (100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr).astype("float64")
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).astype("float64")
        return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0).astype("float64")

    def build_market_snapshot(self, symbol: str) -> Optional[dict]:
        df5 = self.get_rates_dataframe(symbol, mt5.TIMEFRAME_M5)
        df15 = self.get_rates_dataframe(symbol, mt5.TIMEFRAME_M15)
        df60 = self.get_rates_dataframe(symbol, mt5.TIMEFRAME_H1)
        if df5 is None or df15 is None or df60 is None:
            return None

        df15 = df15.copy()
        df60 = df60.copy()

        df15["ema20"] = df15["close"].ewm(span=20, adjust=False).mean()
        df15["ema50"] = df15["close"].ewm(span=50, adjust=False).mean()
        df15["atr14"] = self._atr(df15, 14)
        df15["adx14"] = self._adx(df15, 14)

        df60["ema20"] = df60["close"].ewm(span=20, adjust=False).mean()
        df60["ema50"] = df60["close"].ewm(span=50, adjust=False).mean()
        df60["atr14"] = self._atr(df60, 14)
        df60["adx14"] = self._adx(df60, 14)

        df15 = df15.dropna(subset=["ema20", "ema50", "atr14", "adx14"]).copy()
        df60 = df60.dropna(subset=["ema20", "ema50", "atr14", "adx14"]).copy()
        if len(df5) < 30 or len(df15) < 30 or len(df60) < 30:
            return None

        latest5 = df5.iloc[-1]
        prev5 = df5.iloc[-2]
        latest15 = df15.iloc[-1]
        prev15 = df15.iloc[-2]
        latest60 = df60.iloc[-1]

        pullback_to_ema = (
            latest15["low"] <= latest15["ema20"] <= latest15["high"]
            or latest15["low"] <= latest15["ema50"] <= latest15["high"]
        )

        return {
            "last_candle_time_5m": latest5["time"],
            "last_candle_time_15m": latest15["time"],
            "close_5m": float(latest5["close"]),
            "high_5m": float(latest5["high"]),
            "low_5m": float(latest5["low"]),
            "open_5m": float(latest5["open"]),
            "close_5m_prev": float(prev5["close"]),
            "open_5m_prev": float(prev5["open"]),
            "close_15m": float(latest15["close"]),
            "high_15m": float(latest15["high"]),
            "low_15m": float(latest15["low"]),
            "ema20": float(latest15["ema20"]),
            "ema50": float(latest15["ema50"]),
            "ema20_15_prev3": float(df15["ema20"].iloc[-4]),
            "atr15": float(latest15["atr14"]),
            "atr15_prev": float(prev15["atr14"]),
            "atr15_mean20": float(df15["atr14"].tail(20).mean()),
            "adx15": float(latest15["adx14"]),
            "ema20_60": float(latest60["ema20"]),
            "ema50_60": float(latest60["ema50"]),
            "ema20_60_prev3": float(df60["ema20"].iloc[-4]),
            "atr60": float(latest60["atr14"]),
            "adx60": float(latest60["adx14"]),
            "breakout_high_5": float(df15["high"].iloc[-6:-1].max()),
            "breakout_low_5": float(df15["low"].iloc[-6:-1].min()),
            "volume_15m": float(latest15["tick_volume"]),
            "volume_avg20": float(df15["tick_volume"].tail(20).mean()),
            "pullback_to_ema": bool(pullback_to_ema),
            "ema_distance_atr": abs(float(latest15["ema20"] - latest15["ema50"])) / max(float(latest15["atr14"]), 1e-9),
            "macro_aligned": bool((latest60["ema20"] > latest60["ema50"] and latest15["ema20"] > latest15["ema50"]) or (latest60["ema20"] < latest60["ema50"] and latest15["ema20"] < latest15["ema50"])),
            "rejection_5m": bool((latest5["close"] > latest5["open"] and latest5["low"] < min(latest5["open"], latest5["close"])) or (latest5["close"] < latest5["open"] and latest5["high"] > max(latest5["open"], latest5["close"]))),
        }
