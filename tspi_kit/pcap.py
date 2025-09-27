"""Utilities for replaying TSPI datagrams from PCAP files."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import dpkt


@dataclass
class PCAPPacket:
    timestamp: float
    payload: bytes


class PCAPReplayer:
    """Replay TSPI datagrams from a pcap/pcapng capture."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _iter_packets(self) -> Iterable[PCAPPacket]:
        with self._path.open("rb") as handle:
            reader = dpkt.pcap.Reader(handle)
            for timestamp, payload in reader:
                if len(payload) != 37:
                    continue
                yield PCAPPacket(timestamp=timestamp, payload=payload)

    def replay(
        self,
        producer,
        *,
        rate: float = 1.0,
        base_epoch: float | None = None,
    ) -> List[dict]:
        packets = list(self._iter_packets())
        if not packets:
            return []

        first_timestamp = packets[0].timestamp
        base_epoch = base_epoch if base_epoch is not None else first_timestamp

        results: List[dict] = []
        for packet in packets:
            delta = packet.timestamp - first_timestamp
            recv_time = base_epoch + delta / max(rate, 1e-9)
            result = producer.ingest(packet.payload, recv_time=recv_time)
            results.append(result)
        return results
