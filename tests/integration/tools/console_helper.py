#!/usr/bin/env python3
"""Test helper for emitting control commands without the UI."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Iterable

from tspi_kit.commands import COMMAND_SUBJECT_PREFIX, CommandSender, OpsControlSender
from tspi_kit.jetstream_client import JetStreamThreadedClient
from tspi_kit.tags import TagSender


def _ensure_stream(client: JetStreamThreadedClient, stream: str, subjects: Iterable[str]) -> None:
    normalized = list(dict.fromkeys(subjects))
    client.ensure_stream(stream, normalized)


def _build_client(url: str) -> JetStreamThreadedClient:
    client = JetStreamThreadedClient([url])
    client.start()
    return client


def _send_units(url: str, units: str) -> None:
    client = _build_client(url)
    try:
        subjects = ["tspi.>", f"{COMMAND_SUBJECT_PREFIX}.>", "tags.>", "tspi.ops.ctrl"]
        _ensure_stream(client, "TSPI", subjects)
        sender = CommandSender(client.publisher(), sender_id="integration-tests")
        sender.send_units(units)
    finally:
        client.close()


def _send_tag(url: str, comment: str, timestamp: datetime) -> str:
    client = _build_client(url)
    try:
        subjects = ["tspi.>", f"{COMMAND_SUBJECT_PREFIX}.>", "tags.>", "tspi.ops.ctrl"]
        _ensure_stream(client, "TSPI", subjects)
        sender = TagSender(client.publisher(), sender_id="integration-tests")
        payload = sender.create_tag(comment, timestamp=timestamp)
        return payload.id
    finally:
        client.close()


def _start_replay(url: str, identifier: str, stream: str) -> str:
    client = _build_client(url)
    try:
        subjects = ["tspi.>", f"{COMMAND_SUBJECT_PREFIX}.>", "tags.>", "tspi.ops.ctrl"]
        _ensure_stream(client, "TSPI", subjects)
        ops = OpsControlSender(client.publisher(), sender_id="integration-tests")
        message = ops.start_group_replay(identifier, stream=stream)
        return message.channel.channel_id
    finally:
        client.close()


def _stop_replay(url: str, channel_id: str | None) -> None:
    client = _build_client(url)
    try:
        subjects = ["tspi.>", f"{COMMAND_SUBJECT_PREFIX}.>", "tags.>", "tspi.ops.ctrl"]
        _ensure_stream(client, "TSPI", subjects)
        ops = OpsControlSender(client.publisher(), sender_id="integration-tests")
        ops.stop_group_replay(channel_id)
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit console commands for integration tests")
    parser.add_argument("--nats-server", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    units = sub.add_parser("units", help="Broadcast display units")
    units.add_argument("value", choices=["metric", "imperial"])

    tag = sub.add_parser("tag", help="Publish a tag event")
    tag.add_argument("comment", help="Comment for the tag")
    tag.add_argument(
        "--timestamp",
        default=None,
        help="ISO timestamp for the tag (defaults to current UTC time)",
    )

    replay = sub.add_parser("start-replay", help="Start a group replay")
    replay.add_argument("identifier", help="Replay identifier or timestamp")
    replay.add_argument("--stream", default="TSPI", help="Replay stream backing the channel")

    stop = sub.add_parser("stop-replay", help="Stop a group replay")
    stop.add_argument("channel_id", nargs="?", default=None)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    url = args.nats_server
    if args.command == "units":
        _send_units(url, args.value)
        return 0
    if args.command == "tag":
        if args.timestamp:
            ts = datetime.fromisoformat(args.timestamp.replace("Z", "+00:00")).astimezone(UTC)
        else:
            ts = datetime.now(tz=UTC)
        tag_id = _send_tag(url, args.comment, ts)
        print(tag_id, flush=True)
        return 0
    if args.command == "start-replay":
        channel_id = _start_replay(url, args.identifier, args.stream)
        print(channel_id, flush=True)
        return 0
    if args.command == "stop-replay":
        _stop_replay(url, args.channel_id)
        return 0
    raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
