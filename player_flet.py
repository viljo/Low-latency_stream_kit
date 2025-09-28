from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Mapping

import flet as ft

from tspi_kit.jetstream_client import JetStreamThreadedClient
from tspi_kit.receiver import CompositeTSPIReceiver, TSPIReceiver
from tspi_kit.tags import TagSender
from tspi_kit.ui import HeadlessPlayerRunner, UiConfig
from tspi_kit.ui.flet_app import PlayerViewConfig, mount_player, pick_flet_web_port
from tspi_kit.ui.player import ReceiverFactory, connect_in_memory


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JetStream Player (Flet)")
    defaults = UiConfig()
    parser.add_argument("--headless", action="store_true", help="Run without a GUI")
    parser.add_argument("--metrics-interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--exit-on-idle", type=float)
    parser.add_argument(
        "--json-stream",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Emit player metrics as JSON lines when running headless.",
    )
    parser.add_argument("--write-cbor", type=Path)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--room", type=str, default="default")
    parser.add_argument("--clock", choices=["receive", "tspi"], default="receive")
    parser.add_argument(
        "--source",
        choices=["livestream", "replay.default", "live", "historical"],
        default="livestream",
    )
    parser.add_argument(
        "--smooth-center",
        type=float,
        default=defaults.smooth_center,
        help="Smoothing factor applied to the map center (0.0-1.0).",
    )
    parser.add_argument(
        "--smooth-zoom",
        type=float,
        default=defaults.smooth_zoom,
        help="Smoothing factor applied to the map zoom level (0.0-1.0).",
    )
    parser.add_argument(
        "--window-sec",
        type=int,
        default=defaults.window_sec,
        help="Duration of telemetry retained for map previews (seconds).",
    )
    parser.add_argument(
        "--nats-server",
        dest="nats_servers",
        action="append",
        help="NATS server URL (may be provided multiple times).",
    )
    parser.add_argument(
        "--js-stream",
        default="TSPI",
        help="Name of the JetStream stream that stores live telemetry.",
    )
    parser.add_argument(
        "--historical-stream",
        default=None,
        help="JetStream stream providing historical playback (defaults to auto-discovery).",
    )
    parser.add_argument(
        "--durable-prefix",
        default="player-cli",
        help="Prefix for JetStream durable consumer names.",
    )
    return parser.parse_args(argv)


def _build_sources(
    subject_map: Mapping[str, list[str]],
    *,
    nats_servers: list[str] | None,
    js_stream: str,
    historical_stream: str | None,
    durable_prefix: str,
) -> tuple[
    Mapping[str, ReceiverFactory | TSPIReceiver | CompositeTSPIReceiver],
    TagSender | None,
    Callable[[], None],
]:
    js_client: JetStreamThreadedClient | None = None
    tag_sender: TagSender | None = None
    cleanup = lambda: None

    if nats_servers:
        js_client = JetStreamThreadedClient(nats_servers)
        js_client.start()
        receivers: dict[str, ReceiverFactory | TSPIReceiver | CompositeTSPIReceiver] = {}
        for name, subjects in subject_map.items():
            durable_base = f"{durable_prefix}-{name}"
            stream_name = js_stream if name == "livestream" else historical_stream
            receiver_list: list[TSPIReceiver] = []
            for index, subject in enumerate(subjects):
                durable = f"{durable_base}-{index}"
                consumer = js_client.create_pull_consumer(subject, durable=durable, stream=stream_name)
                receiver_list.append(TSPIReceiver(consumer))
            if len(receiver_list) == 1:
                receivers[name] = receiver_list[0]
            else:
                receivers[name] = CompositeTSPIReceiver(receiver_list)
        tag_sender = TagSender(js_client.publisher(), sender_id="player-ui")

        def _close() -> None:
            js_client.close()

        cleanup = _close
        return receivers, tag_sender, cleanup

    stream, factories = connect_in_memory(subject_map)
    tag_sender = TagSender(stream, sender_id="player-ui")
    return factories, tag_sender, cleanup


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = UiConfig(
        smooth_center=args.smooth_center,
        smooth_zoom=args.smooth_zoom,
        window_sec=args.window_sec,
        metrics_interval=args.metrics_interval,
        default_rate=args.rate,
        default_clock=args.clock,
    )
    subject_map = {
        "livestream": ["tspi.>", "tspi.cmd.display.>", "tags.broadcast"],
        "replay.default": [f"player.{args.room}.playout.>", "tags.broadcast"],
    }

    sources, tag_sender, cleanup = _build_sources(
        subject_map,
        nats_servers=args.nats_servers,
        js_stream=args.js_stream,
        historical_stream=args.historical_stream,
        durable_prefix=args.durable_prefix,
    )

    if args.headless:
        runner = HeadlessPlayerRunner(
            sources,
            ui_config=config,
            stdout_json=args.json_stream,
            duration=args.duration,
            exit_on_idle=args.exit_on_idle,
            write_cbor_dir=args.write_cbor,
            initial_source=args.source,
        )
        runner.run()
        cleanup()
        return 0

    view_config = PlayerViewConfig(ui=config, initial_source=args.source, tag_sender=tag_sender)

    def _launch(page: ft.Page) -> None:
        mount_player(page, sources, config=view_config)

    ft.app(target=_launch, port=pick_flet_web_port())
    cleanup()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
