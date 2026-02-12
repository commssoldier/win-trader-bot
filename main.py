"""Ponto de entrada do WIN Trader Bot."""
from __future__ import annotations

import tkinter as tk

from gui import TradingBotGUI
from logger import setup_logger


def main() -> None:
    logger = setup_logger()
    root = tk.Tk()
    TradingBotGUI(root, logger)
    root.mainloop()


if __name__ == "__main__":
    main()
