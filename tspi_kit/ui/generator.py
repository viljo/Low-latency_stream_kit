"""Qt controller for the TSPI flight generator."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from PyQt5 import QtCore

from ..generator import FlightConfig, TSPIFlightGenerator
from ..producer import TSPIProducer
from .config import UiConfig
from .player import HeadlessPlayerRunner, connect_in_memory


@dataclass(slots=True)
class GeneratorMetrics:
    frames_generated: int = 0
    aircraft: int = 0
    rate: float = 0.0

    def to_json(self) -> str:
        return json.dumps({
            "frames_generated": self.frames_generated,
            "aircraft": self.aircraft,
            "rate": self.rate,
        })


class GeneratorController(QtCore.QObject):
    """Drive the TSPI flight generator and publish to JetStream."""

    metrics_updated = QtCore.pyqtSignal(str)

    def __init__(
        self,
        generator: TSPIFlightGenerator,
        producer: TSPIProducer,
        *,
        ui_config: Optional[UiConfig] = None,
    ) -> None:
        super().__init__()
        self._generator = generator
        self._producer = producer
        self._config = ui_config or UiConfig()
        self._metrics = GeneratorMetrics(
            aircraft=generator._config.count,
            rate=generator._config.rate_hz,
        )

    def run(self, duration: float) -> None:
        frames = int(duration * self._generator._config.rate_hz)
        messages = self._generator.stream_to_producer(self._producer, duration_seconds=duration)
        self._metrics.frames_generated += len(messages)
        self.metrics_updated.emit(self._metrics.to_json())


def build_headless_generator(
    *,
    count: int = 5,
    rate: float = 10.0,
    duration: float = 1.0,
) -> HeadlessPlayerRunner:
    stream, receiver = connect_in_memory()
    producer = TSPIProducer(stream)
    config = FlightConfig(count=count, rate_hz=rate)
    generator = TSPIFlightGenerator(config)
    controller = GeneratorController(generator, producer)
    controller.run(duration)
    return HeadlessPlayerRunner(receiver)
