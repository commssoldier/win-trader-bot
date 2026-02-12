"""Conector de sessão com MetaTrader5."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import MetaTrader5 as mt5


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

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def offline_periods(self) -> list[tuple[datetime, datetime]]:
        return self._offline_periods

    def connect(self, login: int, password: str, server: str) -> bool:
        try:
            if not mt5.initialize(login=login, password=password, server=server):
                self.logger.error("Falha ao inicializar MT5: %s", mt5.last_error())
                self._status = ConnectionStatus(False)
                return False

            account = mt5.account_info()
            if account is None:
                self.logger.error("Falha ao obter dados da conta: %s", mt5.last_error())
                self.disconnect()
                return False

            account_type = "Real" if getattr(account, "trade_mode", 0) == 2 else "Demo"
            self._status = ConnectionStatus(True, account_type, account.login, account.server)
            self.logger.info("Conectado ao MT5. Conta: %s | Tipo: %s", account.login, account_type)
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
        return connected
