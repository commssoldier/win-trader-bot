"""Interface gráfica Tkinter para operação do Sniper Adaptativo."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from engine import TradingEngine
from execution_manager import ExecutionManager
from mt5_connector import MT5Connector
from utils import DEBUG_MODE, TradingWindow, is_within_trading_window, max_contracts, now_b3


class TradingBotGUI:
    def __init__(self, root: tk.Tk, logger) -> None:
        self.root = root
        self.logger = logger
        self.root.title("WIN Trader Bot Pro - Sniper Adaptativo")
        self.root.geometry("980x680")

        self.connector = MT5Connector(logger)
        self.engine: TradingEngine | None = None
        self.window = TradingWindow()

        self.env_var = tk.StringVar(value="Demo")
        self.login_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.server_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Desconectado")
        self.capital_var = tk.StringVar(value="10000")
        self.max_contracts_var = tk.StringVar(value="Máx contratos permitidos: 5")
        self.debug_var = tk.BooleanVar(value=DEBUG_MODE)
        self.regime_var = tk.StringVar(value="NEUTRO")

        self._build_layout()
        self.capital_var.trace_add("write", lambda *_: self._refresh_contract_limit())
        self._refresh_contract_limit()

    def _build_layout(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        auth = ttk.LabelFrame(frame, text="Conexão MT5")
        auth.pack(fill="x", pady=8)

        ttk.Label(auth, text="Ambiente").grid(row=0, column=0, sticky="w")
        ttk.Combobox(auth, textvariable=self.env_var, values=["Demo", "Real"], width=12).grid(row=0, column=1)
        ttk.Label(auth, text="Login").grid(row=0, column=2, sticky="w")
        ttk.Entry(auth, textvariable=self.login_var, width=14).grid(row=0, column=3)
        ttk.Label(auth, text="Senha").grid(row=0, column=4, sticky="w")
        ttk.Entry(auth, textvariable=self.password_var, width=16, show="*").grid(row=0, column=5)
        ttk.Label(auth, text="Servidor").grid(row=0, column=6, sticky="w")
        ttk.Entry(auth, textvariable=self.server_var, width=20).grid(row=0, column=7)

        ttk.Button(auth, text="Conectar", command=self.connect).grid(row=1, column=1, pady=4)
        ttk.Button(auth, text="Desconectar", command=self.disconnect).grid(row=1, column=2, pady=4)
        self.status_lbl = ttk.Label(auth, textvariable=self.status_var)
        self.status_lbl.grid(row=1, column=3, columnspan=4, sticky="w")

        setup = ttk.LabelFrame(frame, text="Sniper Adaptativo")
        setup.pack(fill="x", pady=8)
        ttk.Label(setup, text="Capital").grid(row=0, column=0, sticky="w")
        ttk.Entry(setup, textvariable=self.capital_var, width=14).grid(row=0, column=1)
        ttk.Label(setup, textvariable=self.max_contracts_var).grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Checkbutton(setup, text="Modo Debug", variable=self.debug_var).grid(row=0, column=3, sticky="w", padx=(12, 0))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=8)
        ttk.Button(controls, text="Start", command=self.start_bot).pack(side="left", padx=4)
        ttk.Button(controls, text="Stop", command=self.stop_bot).pack(side="left", padx=4)
        ttk.Label(controls, text="Regime atual:").pack(side="left", padx=(30, 4))
        ttk.Label(controls, textvariable=self.regime_var).pack(side="left")

        logs_frame = ttk.LabelFrame(frame, text="Logs em tempo real")
        logs_frame.pack(fill="both", expand=True)
        self.logs = tk.Text(logs_frame, height=18)
        self.logs.pack(fill="both", expand=True)

    def _refresh_contract_limit(self) -> None:
        try:
            capital = float(self.capital_var.get())
        except ValueError:
            self.max_contracts_var.set("Máx contratos permitidos: 0")
            return
        self.max_contracts_var.set(f"Máx contratos permitidos: {max_contracts(capital)}")

    def _update_runtime_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))

    def _update_regime(self, regime: str) -> None:
        self.root.after(0, lambda: self.regime_var.set(regime))

    def _debug(self, message: str) -> None:
        if self.debug_var.get():
            self._log(f"[DEBUG] {message}")

    def connect(self) -> None:
        self.connector.set_debug(self.debug_var.get(), callback=self._log if self.debug_var.get() else None)
        try:
            ok = self.connector.connect(int(self.login_var.get()), self.password_var.get(), self.server_var.get())
        except ValueError:
            messagebox.showerror("Erro", "Login deve ser numérico.")
            return

        if ok:
            status = self.connector.status
            self.status_var.set(f"Conectado: {status.login} ({status.account_type})")
            if status.account_type == "Real" or self.env_var.get() == "Real":
                self.status_lbl.configure(foreground="red")
                self._log("⚠️ ALERTA: Conta REAL conectada.")
            else:
                self.status_lbl.configure(foreground="green")
                self._log("Conta demo conectada com sucesso.")
        else:
            self.status_var.set("Falha na conexão")
            self.status_lbl.configure(foreground="red")
            self._log("Falha ao conectar ao MT5.")

    def disconnect(self) -> None:
        self.stop_bot()
        self.connector.disconnect()
        self.status_var.set("Desconectado")
        self.status_lbl.configure(foreground="black")
        self._log("Conexão encerrada.")

    def start_bot(self) -> None:
        self._debug("start_bot() acionado")
        if not self.connector.status.connected:
            messagebox.showwarning("Aviso", "Conecte ao MT5 antes de iniciar.")
            return

        try:
            capital = float(self.capital_var.get())
        except ValueError:
            messagebox.showerror("Erro", "Capital deve ser numérico.")
            return

        contracts_limit = max_contracts(capital)
        self._debug(f"Capital informado: {capital}")
        self._debug(f"Máx contratos permitidos: {contracts_limit}")
        if contracts_limit <= 0:
            messagebox.showerror("Erro", "Capital insuficiente. Mínimo R$ 2000 para operar.")
            return

        if not is_within_trading_window(now_b3(), self.window):
            self._log("Fora do horário operacional (10:00–17:00).")
            self.status_var.set("AGUARDANDO HORÁRIO")
            return

        self.connector.set_debug(self.debug_var.get(), callback=self._log if self.debug_var.get() else None)
        self.engine = TradingEngine(
            self.logger,
            self.connector,
            ExecutionManager(self.logger),
            capital,
            debug_mode=self.debug_var.get(),
            debug_callback=self._log if self.debug_var.get() else None,
        )

        self.engine.start(
            contracts=contracts_limit,
            status_callback=self._update_runtime_status,
            regime_callback=self._update_regime,
        )
        self._log("Robô iniciado no modo Sniper Adaptativo.")

    def stop_bot(self) -> None:
        self._debug("stop_bot() acionado")
        if self.engine:
            self.engine.stop()
            self._debug("Thread finalizada com sucesso")
            self._log("Robô parado.")

    def _log(self, text: str) -> None:
        self.logs.insert("end", text + "\n")
        self.logs.see("end")
        self.logger.info(text)
