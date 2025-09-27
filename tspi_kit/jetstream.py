"""JetStream helpers used by the toolkit."""
from __future__ import annotations

from typing import Dict

from .datagrams import ParsedTSPI

_STREAM_PREFIX = "tspi"


def build_subject(message: ParsedTSPI, stream_prefix: str | None = None) -> str:
    """Return the JetStream subject for the parsed datagram."""

    prefix = stream_prefix or _STREAM_PREFIX
    return f"{prefix}.{message.type}.{message.sensor_id}"


def message_headers(message: ParsedTSPI) -> Dict[str, str]:
    """Return JetStream headers for deduplication."""

    return {"Nats-Msg-Id": message.deduplication_id()}
