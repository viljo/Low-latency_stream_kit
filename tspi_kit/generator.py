"""Synthetic TSPI generator utilities for integration testing."""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin, tau
from typing import Iterable, List


@dataclass
class FlightConfig:
    count: int = 50
    rate_hz: float = 50.0
    speed_min_mps: float = 50.0
    speed_max_mps: float = 200.0
    day: int = 120


class TSPIFlightGenerator:
    """Generate deterministic TSPI geocentric datagrams."""

    def __init__(self, config: FlightConfig | None = None) -> None:
        self._config = config or FlightConfig()
        self._dt = 1.0 / self._config.rate_hz
        self._frame_index = 0

    def _sensor_id(self, index: int) -> int:
        return 10_000 + index

    def _header(self, sensor_id: int, time_ticks: int, status: int = 0xFF, flags: int = 0x01) -> bytes:
        import struct

        return struct.pack(
            ">BBHHIBH",
            0xC1,
            4,
            sensor_id,
            self._config.day,
            time_ticks,
            status,
            flags,
        )

    def _payload(self, angle: float, speed: float) -> bytes:
        import struct

        vx = speed * cos(angle)
        vy = speed * sin(angle)
        vz = 5.0
        ax = 0.1 * cos(angle)
        ay = 0.1 * sin(angle)
        az = 0.0
        x = vx * 10
        y = vy * 10
        z = 1000.0

        return struct.pack(
            ">iii hhh hhh".replace(" ", ""),
            int(x * 100),
            int(y * 100),
            int(z * 100),
            int(vx * 100),
            int(vy * 100),
            int(vz * 100),
            int(ax * 100),
            int(ay * 100),
            int(az * 100),
        )

    def generate(self, frames: int) -> Iterable[tuple[bytes, float]]:
        for _ in range(frames):
            time_seconds = self._frame_index * self._dt
            time_ticks = int(time_seconds * 10_000)
            for aircraft in range(self._config.count):
                angle = (aircraft / self._config.count) * tau
                speed = self._config.speed_min_mps + (
                    (self._config.speed_max_mps - self._config.speed_min_mps)
                    * (aircraft / max(self._config.count - 1, 1))
                )
                payload = self._payload(angle + self._frame_index * 0.01, speed)
                header = self._header(self._sensor_id(aircraft), time_ticks)
                yield header + payload, time_seconds
            self._frame_index += 1

    def stream_to_producer(
        self,
        producer,
        duration_seconds: float,
        *,
        base_epoch: float = 1_700_000_000.0,
    ) -> List[dict]:
        frames = int(duration_seconds * self._config.rate_hz)
        results: List[dict] = []
        for datagram, time_seconds in self.generate(frames):
            result = producer.ingest(datagram, recv_time=base_epoch + time_seconds)
            results.append(result)
        return results
