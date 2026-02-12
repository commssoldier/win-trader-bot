# WIN Trader Bot (B3 / MT5)

Robô trader modular em Python para Mini Índice (WIN) com:

- Conexão/autenticação MT5 (Demo/Real)
- GUI Tkinter para operação assistida
- Gestão de risco matemática (meta/stop diário, ATR, R)
- Filtro de volatilidade e classificação de regime
- Regras de bloqueio diário e janela operacional B3
- Relatórios automáticos CSV e curva de equity

## Estrutura

- `main.py`: ponto de entrada
- `gui.py`: interface gráfica
- `mt5_connector.py`: conexão, status e monitoramento offline
- `engine.py`: orquestração das regras de trading
- `regime_detector.py`: classificação de tendência/lateralidade
- `execution_manager.py`: envio de ordens com SL/TP server-side
- `risk_manager.py`: cálculos de risco e limites diários
- `volatility_filter.py`: pausa por volatilidade extrema
- `profile_manager.py`: perfis Conservador/Moderado/Agressivo/Personalizado
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

## Observações de segurança

- A senha MT5 é informada manualmente e não é persistida.
- Conta real gera alerta visual na GUI.
- Todas as ordens devem sair com SL/TP enviados ao servidor MT5.
- Fora do horário (10:00–17:00) e em dia de vencimento (3ª quarta-feira): bloqueio de novas entradas.
