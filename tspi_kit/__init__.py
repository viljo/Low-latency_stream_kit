"""Toolkit primitives for Low-latency Stream Kit."""

from importlib.util import find_spec
from typing import Any

from .datagrams import ParsedTSPI, parse_tspi_datagram
from .schema import load_schema, validate_payload
from .jetstream import build_subject, message_headers
from .jetstream_sim import InMemoryConsumer, InMemoryJetStream, InMemoryJetStreamCluster
from .producer import TSPIProducer
from .receiver import TSPIReceiver
from .pcap import PCAPReplayer
from .generator import FlightConfig, TSPIFlightGenerator

_UI_IMPORT_ERROR: Exception | None = None
_UI_EXPORTS: dict[str, Any] = {}

if find_spec("PyQt5") is not None:
    from .ui import (  # type: ignore[assignment]
        GeneratorController,
        HeadlessPlayerRunner,
        JetStreamPlayerWindow,
        MapPreviewWidget,
        MapSmoother,
        PCAPPlayerController,
        PlayerState,
        UiConfig,
    )

    _UI_EXPORTS.update(
        {
            "UiConfig": UiConfig,
            "MapSmoother": MapSmoother,
            "MapPreviewWidget": MapPreviewWidget,
            "JetStreamPlayerWindow": JetStreamPlayerWindow,
            "HeadlessPlayerRunner": HeadlessPlayerRunner,
            "PlayerState": PlayerState,
            "PCAPPlayerController": PCAPPlayerController,
            "GeneratorController": GeneratorController,
        }
    )
else:  # pragma: no cover - exercised when PyQt5 absent
    _UI_IMPORT_ERROR = ModuleNotFoundError(
        "PyQt5 is required for UI components but is not installed."
    )

__all__ = [
    "ParsedTSPI",
    "parse_tspi_datagram",
    "load_schema",
    "validate_payload",
    "build_subject",
    "message_headers",
    "InMemoryJetStream",
    "InMemoryJetStreamCluster",
    "InMemoryConsumer",
    "TSPIProducer",
    "TSPIReceiver",
    "PCAPReplayer",
    "FlightConfig",
    "TSPIFlightGenerator",
] + sorted(_UI_EXPORTS)


def __getattr__(name: str) -> Any:  # pragma: no cover - exercised in import-time errors
    if name in _UI_EXPORTS:
        return _UI_EXPORTS[name]
    if name in {
        "UiConfig",
        "MapSmoother",
        "MapPreviewWidget",
        "JetStreamPlayerWindow",
        "HeadlessPlayerRunner",
        "PlayerState",
        "PCAPPlayerController",
        "GeneratorController",
    } and _UI_IMPORT_ERROR is not None:
        raise ImportError(str(_UI_IMPORT_ERROR)) from _UI_IMPORT_ERROR
    raise AttributeError(f"module 'tspi_kit' has no attribute {name!r}")
