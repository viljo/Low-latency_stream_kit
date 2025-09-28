"""Flet-based command console for administering TSPI operations."""
from __future__ import annotations

import argparse
import threading
import time
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Sequence

from tspi_kit.commands import (
    COMMAND_SUBJECT_PREFIX,
    CommandSender,
    OpsControlSender,
)
from tspi_kit.commands import OPS_CONTROL_SUBJECT
from tspi_kit.jetstream_client import JetStreamConsumerAdapter, JetStreamThreadedClient
from tspi_kit.tags import TagPayload, TagSender
from tspi_kit.ui.command_console import ClientPresence, ClientPresenceTracker, OperatorEvent
from tspi_kit.ui.flet_app import _ensure_flet, pick_flet_web_port
from tspi_kit.ui.player import connect_in_memory
from tspi_kit.ui.signals import Signal


_COLOR_CHOICES: Sequence[tuple[str, str]] = (
    ("Green", "#00ff00"),
    ("Red", "#ff0000"),
    ("Blue", "#0000ff"),
    ("Magenta", "#ff00ff"),
    ("Yellow", "#ffff00"),
)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


class CommandController:
    """Thin wrapper around the command senders with signal hooks."""

    status_changed: Signal[str]
    error_occurred: Signal[str]
    group_replay_changed: Signal[str]

    def __init__(
        self,
        sender: CommandSender,
        ops_sender: OpsControlSender,
        *,
        tag_sender: TagSender | None = None,
    ) -> None:
        self.status_changed = Signal[str]()
        self.error_occurred = Signal[str]()
        self.group_replay_changed = Signal[str]()
        self._sender = sender
        self._ops_sender = ops_sender
        self._tag_sender = tag_sender

    @property
    def tagging_enabled(self) -> bool:
        return self._tag_sender is not None

    def set_units(self, units: str) -> None:
        try:
            payload = self._sender.send_units(units)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        self.status_changed.emit(f"Units set to {payload.payload['units']}")

    def set_marker_color(self, color: str) -> None:
        try:
            payload = self._sender.send_marker_color(color)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        self.status_changed.emit(f"Marker color set to {payload.payload['marker_color']}")

    def set_session_metadata(self, name: str, identifier: str) -> None:
        try:
            payload = self._sender.send_session_metadata(name, identifier)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        details = payload.payload.get("session_metadata", {})
        session_name = details.get("name", name)
        session_id = details.get("id", identifier)
        self.status_changed.emit(
            f"Session metadata set to {session_name} ({session_id})"
        )

    def create_tag(self, timestamp: datetime, comment: str) -> TagPayload | None:
        if self._tag_sender is None:
            self.error_occurred.emit("Tagging is not configured")
            return None
        try:
            payload = self._tag_sender.create_tag(comment, timestamp=timestamp)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return None
        self.status_changed.emit(
            f"Tag recorded at {payload.ts} — {payload.label}"
        )
        return payload

    def start_group_replay(
        self,
        identifier: str,
        *,
        stream: str,
        display_name: str | None = None,
    ) -> None:
        try:
            message = self._ops_sender.start_group_replay(
                identifier,
                stream=stream,
                display_name=display_name,
            )
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        channel = message.channel
        self.group_replay_changed.emit(channel.channel_id)
        self.status_changed.emit(f"Group replay started on {channel.display_name}")

    def stop_group_replay(self, channel_id: str | None) -> None:
        try:
            message = self._ops_sender.stop_group_replay(channel_id)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        self.group_replay_changed.emit("")
        self.status_changed.emit(f"Group replay stopped on {message.channel_id}")


class StatusPoller:
    """Background helper that polls JetStream status updates."""

    def __init__(
        self,
        consumer: JetStreamConsumerAdapter,
        *,
        poll_interval: float = 0.5,
        batch: int = 32,
        on_message: callable[[bytes], None],
        on_error: callable[[str], None],
    ) -> None:
        self._consumer = consumer
        self._interval = max(0.05, float(poll_interval))
        self._batch = max(1, int(batch))
        self._on_message = on_message
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                messages = self._consumer.pull(self._batch)
            except Exception as exc:  # pragma: no cover - diagnostics only
                self._on_error(str(exc))
                time.sleep(self._interval)
                continue
            if not messages:
                time.sleep(self._interval)
                continue
            for message in messages:
                data = getattr(message, "data", None)
                if data is None:
                    continue
                try:
                    payload = bytes(data)
                except Exception:
                    continue
                self._on_message(payload)


class CommandConsoleApp:
    """Build and manage the Flet command console UI."""

    def __init__(
        self,
        page: Any,
        controller: CommandController,
        *,
        status_consumer: JetStreamConsumerAdapter | None = None,
        default_replay_stream: str = "TSPI",
    ) -> None:
        self._ft = _ensure_flet()
        self.page = page
        self.controller = controller
        self._tracker = ClientPresenceTracker()
        self._pending_tag_timestamp: datetime | None = None
        self._poller: StatusPoller | None = None
        self._clients: Dict[str, ClientPresence] = {}
        self._active_channel: str | None = None
        self._default_replay_stream = default_replay_stream
        self._status_color = self._ft.colors.ON_SURFACE
        self._build_controls()
        self._connect_signals()
        if status_consumer is not None:
            self._poller = StatusPoller(
                status_consumer,
                on_message=lambda payload: self.page.call_from_thread(
                    lambda: self._handle_status_payload(payload)
                ),
                on_error=lambda message: self.page.call_from_thread(
                    lambda: self._append_log(
                        f"Status poller error: {message}",
                        color=self._ft.colors.RED,
                    )
                ),
            )
            self._poller.start()

    # ------------------------------------------------------------------ UI setup

    def _build_controls(self) -> None:
        ft = self._ft
        self.page.title = "Command Console"

        # Display controls
        self.units_dropdown = ft.Dropdown(
            label="Display Units",
            value="metric",
            options=[ft.dropdown.Option("metric"), ft.dropdown.Option("imperial")],
        )
        self.color_dropdown = ft.Dropdown(
            label="Marker Color",
            options=[ft.dropdown.Option(key=value, text=f"{name} ({value})") for name, value in _COLOR_CHOICES],
        )
        self.session_name = ft.TextField(label="Session Name", hint_text="e.g. Falcon Lead")
        self.session_id = ft.TextField(label="Session ID", hint_text="e.g. 42")
        self.tag_timestamp = ft.Text("Press Capture to mark timestamp")
        self.tag_comment = ft.TextField(label="Tag Comment", expand=1, disabled=not self.controller.tagging_enabled)
        self.capture_tag_button = ft.ElevatedButton(
            "Capture Timestamp",
            disabled=not self.controller.tagging_enabled,
            on_click=self._on_capture_tag,
        )
        self.send_tag_button = ft.ElevatedButton(
            "Save/Send",
            disabled=not self.controller.tagging_enabled,
            on_click=self._on_send_tag,
        )

        # Group replay controls
        self.replay_identifier = ft.TextField(
            label="Replay Identifier",
            hint_text="Identifier or computed label",
        )
        self.replay_display = ft.TextField(
            label="Display Name", hint_text="Optional display override"
        )
        self.replay_stream = ft.TextField(
            label="Telemetry Stream",
            value=self._default_replay_stream,
        )
        self.stop_channel_override = ft.TextField(
            label="Stop Channel Override",
            hint_text="Optional channel id",
        )
        self.active_channel = ft.TextField(
            label="Active Channel",
            value="",
            read_only=True,
        )
        self.start_replay_button = ft.ElevatedButton("Start Replay", on_click=self._on_start_replay)
        self.stop_replay_button = ft.OutlinedButton("Stop Replay", on_click=self._on_stop_replay)

        # Status & log
        self.status_text = ft.Text("Ready")
        self.log_view = ft.ListView(expand=1, spacing=6, auto_scroll=True)

        # Client table
        self.client_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Client")),
                ft.DataColumn(ft.Text("Streaming Channel")),
                ft.DataColumn(ft.Text("Status")),
                ft.DataColumn(ft.Text("Connected")),
                ft.DataColumn(ft.Text("Last Seen")),
                ft.DataColumn(ft.Text("Operator")),
                ft.DataColumn(ft.Text("Ping (ms)")),
            ],
            rows=[],
        )

        display_controls = ft.Column(
            [
                ft.Text("Display Controls", style=ft.TextThemeStyle.TITLE_MEDIUM),
                ft.Row([self.units_dropdown, ft.ElevatedButton("Send", on_click=self._on_send_units)]),
                ft.Row([self.color_dropdown, ft.ElevatedButton("Send", on_click=self._on_send_color)]),
                ft.Row([self.session_name, self.session_id, ft.ElevatedButton("Broadcast Metadata", on_click=self._on_send_metadata)]),
                ft.Row([self.capture_tag_button, self.tag_timestamp]),
                ft.Row([self.tag_comment, self.send_tag_button]),
            ],
            spacing=10,
        )

        replay_controls = ft.Column(
            [
                ft.Text("Group Replay", style=ft.TextThemeStyle.TITLE_MEDIUM),
                self.replay_identifier,
                self.replay_display,
                self.replay_stream,
                self.active_channel,
                self.stop_channel_override,
                ft.Row([self.start_replay_button, self.stop_replay_button]),
            ],
            spacing=10,
        )

        layout = ft.Column(
            [
                display_controls,
                replay_controls,
                ft.Text("Operations Log", style=ft.TextThemeStyle.TITLE_MEDIUM),
                ft.Container(self.log_view, height=200),
                ft.Text("Active Clients", style=ft.TextThemeStyle.TITLE_MEDIUM),
                ft.Container(self.client_table, expand=True),
                self.status_text,
            ],
            expand=True,
            spacing=16,
        )

        self.page.add(layout)

    # ------------------------------------------------------------------ Signal wiring

    def _connect_signals(self) -> None:
        self.controller.status_changed.connect(lambda message: self._update_status(message))
        self.controller.error_occurred.connect(lambda message: self._show_error(message))
        self.controller.group_replay_changed.connect(lambda channel: self._set_active_channel(channel))

    # ------------------------------------------------------------------ UI helpers

    def _update_status(self, message: str) -> None:
        self.status_text.value = message
        self.status_text.color = self._status_color
        self._append_log(message)
        self.page.update()

    def _show_error(self, message: str) -> None:
        self.status_text.value = message
        self.status_text.color = self._ft.colors.RED
        self._append_log(f"ERROR: {message}", color=self._ft.colors.RED)
        self.page.update()

    def _set_active_channel(self, channel: str) -> None:
        self._active_channel = channel or None
        self.active_channel.value = channel
        if not channel:
            self.active_channel.value = ""
        self.page.update()

    def _append_log(self, message: str, *, color: str | None = None) -> None:
        entry = self._ft.Text(f"{_format_timestamp(datetime.now(tz=UTC))} — {message}", color=color)
        self.log_view.controls.append(entry)
        if len(self.log_view.controls) > 500:
            del self.log_view.controls[0 : len(self.log_view.controls) - 500]
        self.page.update()

    def _handle_status_payload(self, payload: bytes) -> None:
        presence, events = self._tracker.process_raw(payload)
        if presence is not None:
            self._clients[presence.client_id] = presence
            self._refresh_clients()
        for event in events:
            self._append_event(event)

    def _append_event(self, event: OperatorEvent) -> None:
        timestamp = _format_timestamp(event.timestamp)
        self._append_log(f"{timestamp} — {event.message}")

    def _refresh_clients(self) -> None:
        rows = []
        ft = self._ft
        for presence in sorted(self._clients.values(), key=lambda item: item.client_id):
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(presence.client_id)),
                        ft.DataCell(ft.Text(presence.channel_display)),
                        ft.DataCell(ft.Text(presence.state_display)),
                        ft.DataCell(ft.Text(_format_timestamp(presence.connection_ts))),
                        ft.DataCell(ft.Text(_format_timestamp(presence.last_seen_ts))),
                        ft.DataCell(ft.Text(presence.operator or "")),
                        ft.DataCell(
                            ft.Text(
                                f"{presence.ping_ms:.1f}" if presence.ping_ms is not None else ""
                            )
                        ),
                    ]
                )
            )
        self.client_table.rows = rows
        self.page.update()

    # ------------------------------------------------------------------ Event handlers

    async def _on_send_units(self, _event) -> None:
        value = self.units_dropdown.value or "metric"
        self.controller.set_units(value)

    async def _on_send_color(self, _event) -> None:
        value = self.color_dropdown.value
        if not value and self.color_dropdown.options:
            value = self.color_dropdown.options[0].key
        if isinstance(value, str):
            self.controller.set_marker_color(value)

    async def _on_send_metadata(self, _event) -> None:
        name = self.session_name.value.strip() if self.session_name.value else ""
        identifier = self.session_id.value.strip() if self.session_id.value else ""
        self.controller.set_session_metadata(name, identifier)

    async def _on_capture_tag(self, _event) -> None:
        if not self.controller.tagging_enabled:
            self._show_error("Tagging is not configured")
            return
        timestamp = datetime.now(tz=UTC)
        self._pending_tag_timestamp = timestamp
        self.tag_timestamp.value = _format_timestamp(timestamp)
        self.tag_comment.disabled = False
        self.tag_comment.value = ""
        self.send_tag_button.disabled = False
        self.page.update()

    async def _on_send_tag(self, _event) -> None:
        if not self.controller.tagging_enabled:
            self._show_error("Tagging is not configured")
            return
        if self._pending_tag_timestamp is None:
            self._show_error("Capture a timestamp before saving a tag")
            return
        comment = (self.tag_comment.value or "").strip()
        if not comment:
            self._show_error("Enter a tag comment before saving")
            return
        payload = self.controller.create_tag(self._pending_tag_timestamp, comment)
        if payload is None:
            return
        self.tag_comment.value = ""
        self.tag_comment.disabled = True
        self.send_tag_button.disabled = True
        self.tag_timestamp.value = "Press Capture to mark timestamp"
        self._pending_tag_timestamp = None
        self.page.update()

    async def _on_start_replay(self, _event) -> None:
        identifier = (self.replay_identifier.value or "").strip()
        if not identifier:
            self._show_error("Enter a replay identifier")
            return
        stream = (self.replay_stream.value or self._default_replay_stream).strip()
        display_name = (self.replay_display.value or "").strip() or None
        self.controller.start_group_replay(identifier, stream=stream, display_name=display_name)

    async def _on_stop_replay(self, _event) -> None:
        override = (self.stop_channel_override.value or "").strip() or None
        channel = override or self._active_channel
        self.controller.stop_group_replay(channel)

    # ------------------------------------------------------------------ Lifecycle

    def shutdown(self) -> None:
        if self._poller is not None:
            self._poller.stop()


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TSPI Command Console (Flet)")
    parser.add_argument("--headless", action="store_true", help="Run without launching the UI")
    parser.add_argument("--nats-server", dest="nats_servers", action="append", help="NATS server URL (repeatable)")
    parser.add_argument("--js-stream", default="TSPI", help="JetStream stream storing telemetry")
    parser.add_argument("--ops-stream", default="TSPI.ops", help="JetStream stream for operator events")
    parser.add_argument("--stream-prefix", default="tspi", help="Telemetry subject prefix for live publishing")
    parser.add_argument("--status-subject", default="tspi.ops.status", help="Subject to monitor for status heartbeats")
    parser.add_argument("--sender-id", default="command-console", help="Identifier used when emitting commands")
    parser.add_argument(
        "--group-replay-id",
        help="Start a group replay using the provided datastore identifier when running headless",
    )
    parser.add_argument(
        "--group-replay-stream",
        help="Stream backing historical telemetry replays (defaults to --js-stream)",
    )
    parser.add_argument(
        "--stop-group-replay",
        nargs="?",
        const="",
        help="Stop an active group replay (optionally specifying a channel id)",
    )
    parser.add_argument("--units", choices=["metric", "imperial"], help="Broadcast display units in headless mode")
    parser.add_argument("--marker-color", help="Broadcast marker color in headless mode")
    parser.add_argument("--session-name", help="Broadcast session name in headless mode")
    parser.add_argument("--session-id", help="Broadcast session identifier in headless mode")
    return parser.parse_args(argv)


def _create_status_consumer(
    js_client: JetStreamThreadedClient,
    *,
    status_subject: str,
    ops_stream: str,
) -> Optional[JetStreamConsumerAdapter]:
    try:
        js_client.ensure_stream(ops_stream, [status_subject])
        return js_client.create_pull_consumer(status_subject, stream=ops_stream)
    except Exception:  # pragma: no cover - stream may already exist without permissions
        try:
            return js_client.create_pull_consumer(status_subject, stream=ops_stream)
        except Exception:
            return None


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    js_client: JetStreamThreadedClient | None = None
    status_consumer = None
    tag_sender: TagSender | None = None

    if args.nats_servers:
        js_client = JetStreamThreadedClient(args.nats_servers)
        js_client.start()
        subjects = [
            f"{args.stream_prefix}.>",
            f"{COMMAND_SUBJECT_PREFIX}.>",
            "tags.>",
            OPS_CONTROL_SUBJECT,
        ]
        js_client.ensure_stream(args.js_stream, subjects)
        status_consumer = _create_status_consumer(
            js_client,
            status_subject=args.status_subject,
            ops_stream=args.ops_stream,
        )
        publisher = js_client.publisher()
        tag_sender = TagSender(publisher, sender_id=args.sender_id)
    else:
        stream, _ = connect_in_memory(
            {
                "live": [
                    "tspi.>",
                    f"{COMMAND_SUBJECT_PREFIX}.>",
                    OPS_CONTROL_SUBJECT,
                    args.status_subject,
                ],
            }
        )
        publisher = stream
        tag_sender = TagSender(stream, sender_id=args.sender_id)
        try:
            status_consumer = stream.create_consumer(args.status_subject)
        except Exception:
            status_consumer = None

    sender = CommandSender(publisher, sender_id=args.sender_id)
    ops_sender = OpsControlSender(publisher, sender_id=args.sender_id)

    if args.headless:
        requested_metadata = args.session_name or args.session_id
        if requested_metadata and not (args.session_name and args.session_id):
            raise SystemExit(
                "Headless session metadata broadcast requires both --session-name and --session-id"
            )
        replay_requested = bool(args.group_replay_id) or args.stop_group_replay is not None
        if (
            not args.units
            and not args.marker_color
            and not requested_metadata
            and not replay_requested
        ):
            raise SystemExit(
                "Headless mode requires a display command, session metadata, or group replay command"
            )
        if args.units:
            sender.send_units(args.units)
        if args.marker_color:
            sender.send_marker_color(args.marker_color)
        if args.session_name and args.session_id:
            sender.send_session_metadata(args.session_name, args.session_id)
        if args.group_replay_id:
            stream_name = args.group_replay_stream or args.js_stream
            ops_sender.start_group_replay(
                args.group_replay_id,
                stream=stream_name,
            )
        if args.stop_group_replay is not None:
            channel_id = args.stop_group_replay or None
            ops_sender.stop_group_replay(channel_id)
        if js_client is not None:
            js_client.close()
        return 0

    controller = CommandController(sender, ops_sender, tag_sender=tag_sender)
    console_holder: Dict[str, CommandConsoleApp] = {}
    ft = _ensure_flet()

    def _launch(page) -> None:
        console = CommandConsoleApp(
            page,
            controller,
            status_consumer=status_consumer,
            default_replay_stream=args.group_replay_stream or args.js_stream,
        )
        console_holder["app"] = console

        def _cleanup(_event: Any | None = None) -> None:
            console.shutdown()
            if js_client is not None:
                js_client.close()

        page.on_close = _cleanup
        page.on_disconnect = _cleanup

    ft.app(target=_launch, port=pick_flet_web_port())
    app = console_holder.get("app")
    if app is not None:
        app.shutdown()
    if js_client is not None:
        js_client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())

