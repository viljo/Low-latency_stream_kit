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
    parser.add_argument("--stdout-json", action="store_true", default=True)
    parser.add_argument("--write-cbor", type=Path)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--room", type=str, default="default")
    parser.add_argument("--clock", choices=["receive", "tspi"], default="receive")
    parser.add_argument("--source", choices=["live", "historical"], default="live")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = UiConfig(metrics_interval=args.metrics_interval, default_rate=args.rate, default_clock=args.clock)
    ensure_offscreen(args.headless)
    subject_map = {
        "live": "tspi.>",
        "historical": f"player.{args.room}.playout.>",
    }
    stream, sources = connect_in_memory(subject_map)

    if args.headless:
        runner = HeadlessPlayerRunner(
            sources,
            ui_config=config,
            stdout_json=args.stdout_json,
            duration=args.duration,
            exit_on_idle=args.exit_on_idle,
            write_cbor_dir=args.write_cbor,
            initial_source=args.source,
        )
        runner.run()
        return 0

    app = QtWidgets.QApplication(sys.argv)
    window = JetStreamPlayerWindow(sources, ui_config=config, initial_source=args.source)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
