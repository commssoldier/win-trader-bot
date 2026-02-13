"""Geração de relatório diário e métricas."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from execution_manager import ExecutedTrade


class ReportGenerator:
    def __init__(self, base_dir: str = "reports") -> None:
        self.base = Path(base_dir)

    def generate_daily_report(
        self,
        report_date: date,
        strategy_name: str,
        capital: float,
        result_reais: float,
        result_points: float,
        trades: list[ExecutedTrade],
        drawdown: float,
        offline_count: int,
        offline_duration_min: float,
    ) -> Path:
        self.base.mkdir(parents=True, exist_ok=True)
        path = self.base / f"{report_date.isoformat()}_WIN_report.csv"
        wins = len([t for t in trades if t.result_reais > 0])
        win_rate = (wins / len(trades) * 100) if trades else 0.0

        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["Resumo geral"])
            writer.writerow(["Data", report_date.isoformat()])
            writer.writerow(["Estratégia", strategy_name])
            writer.writerow(["Capital inicial", f"{capital:.2f}"])
            writer.writerow(["Resultado (R$)", f"{result_reais:.2f}"])
            writer.writerow(["Resultado (pontos)", f"{result_points:.2f}"])
            writer.writerow(["Win rate", f"{win_rate:.2f}%"])
            writer.writerow(["Drawdown máximo (R$)", f"{drawdown:.2f}"])
            writer.writerow(["Conexões offline", offline_count])
            writer.writerow(["Tempo offline (min)", f"{offline_duration_min:.2f}"])
            writer.writerow([])
            writer.writerow(["Detalhamento por operação"])
            writer.writerow(
                [
                    "Entrada",
                    "Saída",
                    "Tipo",
                    "Contratos",
                    "Preço entrada",
                    "Preço saída",
                    "Stop pts",
                    "Take pts",
                    "Resultado (pts)",
                    "R$",
                    "Regime",
                    "Motivo saída",
                ]
            )
            for t in trades:
                writer.writerow(
                    [
                        t.entry_time.isoformat(),
                        t.exit_time.isoformat() if t.exit_time else "",
                        t.side,
                        t.contracts,
                        t.entry_price,
                        t.exit_price or "",
                        t.stop_pts,
                        t.take_pts,
                        t.result_points,
                        t.result_reais,
                        t.regime,
                        t.exit_reason,
                    ]
                )
        return path
