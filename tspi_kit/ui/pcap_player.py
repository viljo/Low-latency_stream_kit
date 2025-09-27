"""Qt controller for the PCAP player."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from PyQt5 import QtCore

from ..pcap import PCAPReplayer
from ..producer import TSPIProducer
from .config import UiConfig
from .player import HeadlessPlayerRunner, connect_in_memory


@dataclass(slots=True)
class PCAPMetrics:
    frames_sent: int = 0
    rate: float = 1.0
    loop: bool = False

    def to_json(self) -> str:
        return json.dumps({
            "frames_sent": self.frames_sent,
            "rate": self.rate,
            "loop": self.loop,
        })


class PCAPPlayerController(QtCore.QObject):
    """Headless-friendly PCAP replay controller."""

    metrics_updated = QtCore.pyqtSignal(str)

    def __init__(
        self,
        replayer: PCAPReplayer,
        producer: TSPIProducer,
        *,
        rate: float = 1.0,
        loop: bool = False,
        ui_config: Optional[UiConfig] = None,
    ) -> None:
        super().__init__()
        self._replayer = replayer
        self._producer = producer
        self._rate = rate
        self._loop = loop
        self._config = ui_config or UiConfig()
        self._metrics = PCAPMetrics(rate=rate, loop=loop)

    def replay(self) -> None:
        packets = self._replayer.replay(self._producer, rate=self._rate, base_epoch=time.time())
        self._metrics.frames_sent += len(packets)
        self.metrics_updated.emit(self._metrics.to_json())
        if self._loop and packets:
            self.replay()


def build_headless_player_from_pcap(path, *, rate: float = 1.0) -> HeadlessPlayerRunner:
    stream, receiver = connect_in_memory()
    producer = TSPIProducer(stream)
    replayer = PCAPReplayer(path)
    controller = PCAPPlayerController(replayer, producer, rate=rate)
    controller.replay()
    return HeadlessPlayerRunner(receiver)
