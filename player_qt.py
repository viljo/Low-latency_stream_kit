"""Entry point for the JetStream Player Qt application."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt5 import QtWidgets

from tspi_kit.ui import HeadlessPlayerRunner, JetStreamPlayerWindow, UiConfig
from tspi_kit.ui.player import connect_in_memory, ensure_offscreen


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JetStream Player")
    parser.add_argument("--headless", action="store_true", help="Run without a GUI")
    parser.add_argument("--metrics-interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--exit-on-idle", type=float)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--stdout-json",
        dest="stdout_json",
        action="store_true",
        default=True,
        help="Enable JSON metrics on stdout (default)",
    )
    group.add_argument(
        "--no-stdout-json",
        dest="stdout_json",
        action="store_false",
        help="Disable JSON metrics on stdout",
    )
    parser.add_argument("--write-cbor", type=Path)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--room", type=str, default="default")
    parser.add_argument("--clock", choices=["receive", "tspi"], default="receive")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = UiConfig(metrics_interval=args.metrics_interval, default_rate=args.rate, default_clock=args.clock)
    ensure_offscreen(args.headless)
    stream, receiver = connect_in_memory()

    if args.headless:
        runner = HeadlessPlayerRunner(
            receiver,
            ui_config=config,
            stdout_json=args.stdout_json,
            duration=args.duration,
            exit_on_idle=args.exit_on_idle,
            write_cbor_dir=args.write_cbor,
        )
        runner.run()
        return 0

    app = QtWidgets.QApplication(sys.argv)
    window = JetStreamPlayerWindow(receiver, ui_config=config)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
