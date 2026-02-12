"""Funções utilitárias compartilhadas."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from math import floor
from zoneinfo import ZoneInfo

B3_TZ = ZoneInfo("America/Sao_Paulo")
WIN_POINT_VALUE = 0.20


@dataclass
class TradingWindow:
    """Janela de operação do robô."""

    start: time = time(10, 0)
    end: time = time(17, 0)
    force_close: time = time(17, 30)


def now_b3() -> datetime:
    """Retorna timestamp atual no fuso da B3."""
    return datetime.now(tz=B3_TZ)


def is_within_trading_window(moment: datetime, window: TradingWindow) -> bool:
    """Valida se está dentro da janela padrão de operação."""
    local_time = moment.timetz().replace(tzinfo=None)
    return window.start <= local_time <= window.end


def third_wednesday(input_date: date) -> date:
    """Retorna a terceira quarta-feira do mês da data informada."""
    first = input_date.replace(day=1)
    while first.weekday() != 2:
        first += timedelta(days=1)
    return first + timedelta(days=14)


def is_expiration_day(input_date: date) -> bool:
    """Detecta se a data atual é dia de vencimento de índice."""
    return input_date == third_wednesday(input_date)


def reais_to_points(value_reais: float) -> float:
    """Converte valor em reais para pontos WIN."""
    return value_reais / WIN_POINT_VALUE


def points_to_reais(points: float) -> float:
    """Converte pontos WIN para reais."""
    return points * WIN_POINT_VALUE


def max_contracts(capital: float) -> int:
    """Calcula quantidade máxima de contratos por capital."""
    return max(1, floor(capital / 2000.0))
