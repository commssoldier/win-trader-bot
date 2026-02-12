"""Persistência de curva de equity e métricas acumuladas."""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class EquityPoint:
    timestamp: datetime
    equity_reais: float
    expectancy_reais: float


class EquityTracker:
    """Controla curva de equity e estatísticas mensais."""

    def __init__(self) -> None:
        self.history: list[EquityPoint] = []

    def add(self, equity_reais: float, total_trades: int, total_profit: float) -> None:
        expectancy = (total_profit / total_trades) if total_trades > 0 else 0.0
        self.history.append(EquityPoint(datetime.now(), equity_reais, expectancy))

    def export_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["timestamp", "equity_reais", "expectancy_reais"])
            for p in self.history:
                writer.writerow([p.timestamp.isoformat(), f"{p.equity_reais:.2f}", f"{p.expectancy_reais:.2f}"])


    def export_monthly_stats(self, path: Path) -> None:
        """Gera estatísticas mensais automáticas de performance."""
        buckets = defaultdict(list)
        for point in self.history:
            key = point.timestamp.strftime("%Y-%m")
            buckets[key].append(point.equity_reais)

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["mes", "equity_inicio", "equity_fim", "variacao"])
            for month, values in sorted(buckets.items()):
                writer.writerow([month, f"{values[0]:.2f}", f"{values[-1]:.2f}", f"{(values[-1]-values[0]):.2f}"])
