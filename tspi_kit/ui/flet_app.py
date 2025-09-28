"""Flet UI for the JetStream player."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import ModuleType
from typing import Mapping, Optional

try:
    import flet as ft
except ModuleNotFoundError:  # pragma: no cover - exercised when optional dep missing
    ft = None  # type: ignore[assignment]

from ..receiver import CompositeTSPIReceiver, TSPIReceiver
from ..tags import TagSender
from .config import UiConfig
from .map import MapPreviewWidget, MapSmoother
from .player import PlayerMetrics, PlayerState, ReceiverFactory


@dataclass
class PlayerViewConfig:
    """Configuration wrapper for the Flet view."""

    ui: UiConfig
    initial_source: str = "live"
    tag_sender: Optional[TagSender] = None


def _ensure_flet() -> ModuleType:
    """Return the imported :mod:`flet` module or raise a helpful error."""

    global ft
    if ft is None:  # pragma: no cover - exercised when optional dep missing
        try:
            import flet as _ft
        except ModuleNotFoundError as exc:  # pragma: no cover - same as above
            raise ModuleNotFoundError(
                "The optional dependency 'flet' is required for the JetStream player UI."
            ) from exc
        ft = _ft
    return ft


class JetStreamPlayerApp:
    """Build and manage the JetStream player inside a Flet page."""

    def __init__(
        self,
        page: ft.Page,
        sources: Mapping[str, ReceiverFactory | TSPIReceiver | CompositeTSPIReceiver],
        *,
        config: Optional[PlayerViewConfig] = None,
    ) -> None:
        _ensure_flet()
        self.page = page
        cfg = config or PlayerViewConfig(ui=UiConfig())
        self._tag_sender = cfg.tag_sender
        self._map_widget = MapPreviewWidget(
            MapSmoother(smooth_center=cfg.ui.smooth_center, smooth_zoom=cfg.ui.smooth_zoom)
        )
        self.state = PlayerState(
            sources,
            ui_config=cfg.ui,
            initial_source=cfg.initial_source,
            map_widget=self._map_widget,
        )
        self._loop_task: asyncio.Task[None] | None = None
        self._build_controls(cfg)
        self._connect_signals()
        self.state.preload()
        self._refresh_map()

    # ------------------------------------------------------------------ UI setup

    def _build_controls(self, cfg: PlayerViewConfig) -> None:
        self.page.title = "JetStream Player"
        self.play_button = ft.ElevatedButton("Play", on_click=self._on_toggle_play)
        self.seek_field = ft.TextField(label="Seek (ISO timestamp)")
        self.seek_button = ft.ElevatedButton("Seek", on_click=self._on_seek)
        self.rate_slider = ft.Slider(
            min=cfg.ui.rate_min,
            max=cfg.ui.rate_max,
            value=self.state.rate,
            divisions=50,
            label="{value:.2f}x",
            on_change=self._on_rate_change,
        )
        self.clock_dropdown = ft.Dropdown(
            label="Clock",
            value=self.state.clock_source,
            options=[ft.dropdown.Option("receive"), ft.dropdown.Option("tspi")],
            on_change=self._on_clock_change,
        )
        source_options = [
            ft.dropdown.Option(key=channel, text=label)
            for label, channel in self.state.channel_options()
        ]
        self.source_dropdown = ft.Dropdown(
            label="Source",
            value=self.state.current_channel,
            options=source_options,
            on_change=self._on_source_change,
        )
        self.units_text = ft.Text(f"Units: {self.state.display_units}")
        self.marker_text = ft.Text(f"Marker: {self.state.marker_color}")
        self.metrics_text = ft.Text("Frames: 0")
        self.map_text = ft.Text(self._map_widget.position_text)
        self.log_list = ft.ListView(expand=1, spacing=4)
        self.tag_list = ft.ListView(expand=1, spacing=4)
        tag_controls: list[ft.Control] = []
        if self._tag_sender is not None:
            self.tag_comment = ft.TextField(label="Tag comment", expand=1)
            self.tag_button = ft.ElevatedButton("Save/Send", on_click=self._on_send_tag)
            tag_controls = [self.tag_comment, self.tag_button]
        controls = ft.Column(
            [
                ft.Row([self.play_button, self.seek_field, self.seek_button]),
                ft.Row([self.rate_slider, self.clock_dropdown, self.source_dropdown]),
                ft.Row([self.units_text, self.marker_text]),
                self.map_text,
                self.metrics_text,
                ft.Text("Command & Tag Log"),
                self.log_list,
                ft.Text("Tags"),
                self.tag_list,
            ]
        )
        if tag_controls:
            controls.controls.insert(4, ft.Row(tag_controls))
        self.page.add(controls)
        self.page.update()

    # ------------------------------------------------------------------ Signal handlers

    def _connect_signals(self) -> None:
        self.state.display_units_changed.connect(lambda units: self._update_text(self.units_text, f"Units: {units}"))
        self.state.marker_color_changed.connect(lambda color: self._update_marker(color))
        self.state.metrics_updated.connect(self._on_metrics)
        self.state.command_event.connect(self._on_command_event)
        self.state.tag_event.connect(self._on_tag_event)
        self._map_widget.state_changed.connect(lambda _: self._refresh_map())

    def _update_text(self, control: ft.Text, value: str) -> None:
        control.value = value
        self.page.update()

    def _update_marker(self, color: str) -> None:
        self.marker_text.value = f"Marker: {color}"
        self.page.update()

    def _refresh_map(self) -> None:
        self.map_text.value = self._map_widget.position_text
        self.page.update()

    # ------------------------------------------------------------------ Event callbacks

    async def _on_toggle_play(self, event: ft.ControlEvent) -> None:
        if self.state.playing:
            self.state.pause()
            self.play_button.text = "Play"
        else:
            self.state.start()
            self.play_button.text = "Pause"
            self._ensure_loop()
        await self.page.update_async()

    async def _on_seek(self, event: ft.ControlEvent) -> None:
        self.state.seek(self.seek_field.value)
        await self.page.update_async()

    async def _on_rate_change(self, event: ft.ControlEvent) -> None:
        self.state.set_rate(float(event.control.value))
        await self.page.update_async()

    async def _on_clock_change(self, event: ft.ControlEvent) -> None:
        self.state.set_clock_source(str(event.control.value))
        await self.page.update_async()

    async def _on_source_change(self, event: ft.ControlEvent) -> None:
        value = str(event.control.value)
        self.state.set_channel(value)
        await self.page.update_async()

    async def _on_send_tag(self, event: ft.ControlEvent) -> None:
        if self._tag_sender is None:
            return
        comment = self.tag_comment.value.strip()
        if not comment:
            return
        payload = self._tag_sender.create_tag(comment)
        self.log_list.controls.append(ft.Text(f"[TAG] {payload.ts} — {payload.label}"))
        self.tag_comment.value = ""
        await self.page.update_async()

    def _on_metrics(self, metrics: PlayerMetrics) -> None:
        self.metrics_text.value = f"Frames: {metrics.frames} | Rate: {metrics.rate:.2f}"
        self._append_log(ft.Text(f"[METRICS] {metrics.frames} frames"))
        self.page.update()

    def _on_command_event(self, message) -> None:
        summary = str(message.get("name", "command"))
        timestamp = self._extract_timestamp(message)
        label = f"[CMD] {timestamp} — {summary}" if timestamp else f"[CMD] {summary}"
        self._append_log(ft.Text(label))

    def _on_tag_event(self, message) -> None:
        tag_id = str(message.get("id", "")).strip()
        if not tag_id:
            return
        label = message.get("label") or tag_id
        status = str(message.get("status", "")).lower()
        display = f"{label} ({tag_id})"
        existing = [ctrl for ctrl in self.tag_list.controls if getattr(ctrl, "data", None) == tag_id]
        if status == "deleted":
            for ctrl in existing:
                self.tag_list.controls.remove(ctrl)
        else:
            if existing:
                existing[0].value = display
            else:
                text = ft.Text(display)
                text.data = tag_id
                self.tag_list.controls.append(text)
        self.page.update()

    def _append_log(self, entry: ft.Text) -> None:
        self.log_list.controls.append(entry)
        while len(self.log_list.controls) > 200:
            self.log_list.controls.pop(0)
        self.page.update()

    def _ensure_loop(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._player_loop())

    async def _player_loop(self) -> None:
        try:
            while self.state.playing:
                self.state.step_once()
                await asyncio.sleep(max(0.01, 0.05 / max(self.state.rate, 0.01)))
        finally:
            self._loop_task = None
            if not self.state.playing:
                self.play_button.text = "Play"
            await self.page.update_async()

    @staticmethod
    def _extract_timestamp(message) -> str:
        for key in ("recv_iso", "ts", "updated_ts"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""


def mount_player(
    page: ft.Page,
    sources: Mapping[str, ReceiverFactory | TSPIReceiver | CompositeTSPIReceiver],
    *,
    config: Optional[PlayerViewConfig] = None,
) -> JetStreamPlayerApp:
    """Convenience helper used by the CLI entry point."""

    return JetStreamPlayerApp(page, sources, config=config)


__all__ = ["JetStreamPlayerApp", "PlayerViewConfig", "mount_player"]
