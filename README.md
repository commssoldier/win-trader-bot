# WIN Trader Bot (B3 / MT5)

Robô trader modular em Python para Mini Índice (WIN) no modelo **Sniper Adaptativo**:

- Conexão/autenticação MT5 (Demo/Real)
- GUI Tkinter simplificada
- Detecção hierárquica de regime (60m + 15m)
- Engine orientado a eventos de candle (15m para regime e 5m para entrada)
- Simulação de sinais com stop/take/trailing baseados em ATR
- Relatórios automáticos CSV e curva de equity

## Estrutura

- `main.py`: ponto de entrada
- `gui.py`: interface gráfica
- `mt5_connector.py`: conexão, status e snapshots
- `engine.py`: orquestração do loop e regras de entrada
- `regime_detector.py`: classificação de mercado
- `execution_manager.py`: camada de execução (não usada para ordens reais no modo atual)
- `risk_manager.py`: sizing e níveis de risco por regime
- `volatility_filter.py`: utilitário de volatilidade
- `report_generator.py`: relatório diário de trades
- `equity_tracker.py`: curva de equity e expectativa
- `logger.py`: logging central
- `utils.py`: horários, vencimento e conversões

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Execução

```bash
python main.py
```

## Observações

- A senha MT5 é informada manualmente e não é persistida.
- Conta real gera alerta visual na GUI.
- Fora do horário (10:00–17:00) e em dia de vencimento (3ª quarta-feira): bloqueio de entradas.
