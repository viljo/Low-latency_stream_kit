"""Integration test exercising a demo-like end-to-end workflow."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, List, Optional

import cbor2

from tspi_kit import (
    Archiver,
    CommandSender,
    FlightConfig,
    InMemoryJetStream,
    StoreReplayer,
    TSPIFlightGenerator,
    TSPIProducer,
)
from tspi_kit.jetstream_sim import JetStreamMessage, _match_subject

_DEMO_PATH = Path(__file__).resolve().parents[1] / "demo"
_DEMO_LOADER = importlib.machinery.SourceFileLoader("_demo_module", str(_DEMO_PATH))
_DEMO_SPEC = importlib.util.spec_from_loader("_demo_module", _DEMO_LOADER)
if _DEMO_SPEC is None:  # pragma: no cover - defensive import guard
    raise RuntimeError("Unable to load demo helper module for integration test")
_DEMO_MODULE = importlib.util.module_from_spec(_DEMO_SPEC)
_DEMO_LOADER.exec_module(_DEMO_MODULE)
InMemoryTimescaleStore = _DEMO_MODULE.InMemoryTimescaleStore


class _PublishResult:
    """Awaitable wrapper allowing synchronous + async publish semantics."""

    def __init__(self, succeeded: bool) -> None:
        self._succeeded = succeeded

    def __await__(self) -> Iterable[bool]:
        if False:  # pragma: no cover - generator trick to create an awaitable
            yield self._succeeded
        return self._succeeded

    def __bool__(self) -> bool:  # pragma: no cover - behaviour verified by callers
        return self._succeeded


class _AsyncMessage:
    """Adapter matching the subset of ``nats.js`` message APIs used by the archiver."""

    def __init__(self, message: JetStreamMessage) -> None:
        self.subject = message.subject
        self.data = message.data
        self.headers = dict(message.headers)
        timestamp = datetime.fromtimestamp(message.timestamp, tz=UTC)
        self.metadata = SimpleNamespace(timestamp=timestamp)

    async def ack(self) -> None:  # pragma: no cover - parity with real client
        return None


class _AsyncSubscription:
    """Simplified pull subscription compatible with :class:`Archiver`."""

    def __init__(self, stream: "_DemoLikeJetStream", subject_filter: str) -> None:
        self._stream = stream
        self._subject_filter = subject_filter
        self._cursor = 0

    async def fetch(self, batch: int, *, timeout: Optional[float] = None) -> List[_AsyncMessage]:  # noqa: ARG002
        messages: List[_AsyncMessage] = []
        while len(messages) < batch and self._cursor < len(self._stream._messages):
            candidate = self._stream._messages[self._cursor]
            self._cursor += 1
            if _match_subject(candidate.subject, self._subject_filter):
                messages.append(_AsyncMessage(candidate))
        return messages


class _DemoLikeJetStream(InMemoryJetStream):
    """Hybrid JetStream stub mixing synchronous and asynchronous APIs."""

    def publish(  # type: ignore[override]
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Optional[dict[str, str]] = None,
        timestamp: Optional[float] = None,
    ) -> _PublishResult:
        succeeded = super().publish(subject, payload, headers=headers, timestamp=timestamp)
        return _PublishResult(succeeded)

    async def pull_subscribe(
        self, subject: str, *, durable: Optional[str] = None  # noqa: ARG002 - parity with real client
    ) -> _AsyncSubscription:
        return _AsyncSubscription(self, subject)


async def _run_demo_like_flow() -> None:
    base_epoch = 1_700_100_000.0
    jetstream = _DemoLikeJetStream()
    producer = TSPIProducer(jetstream)
    generator = TSPIFlightGenerator(FlightConfig(count=2, rate_hz=4.0, speed_min_mps=80.0, speed_max_mps=140.0))

    telemetry_payloads = generator.stream_to_producer(
        producer, duration_seconds=0.5, base_epoch=base_epoch
    )
    telemetry_count = len(telemetry_payloads)
    assert telemetry_count > 0

    sender = CommandSender(jetstream, sender_id="demo-test")
    command = sender.send_units("metric")

    tag_payload = {
        "id": "tag-1",
        "ts": datetime.fromtimestamp(base_epoch, tz=UTC).isoformat(),
        "label": "Integration Test",
        "creator": "pytest",
        "status": "active",
        "extra": {"source": "demo-like"},
    }
    jetstream.publish(
        "tags.demo.created",
        cbor2.dumps(tag_payload),
        headers={"Nats-Msg-Id": "tag-1"},
        timestamp=base_epoch + 0.25,
    )

    datastore = InMemoryTimescaleStore()
    archiver = Archiver(jetstream, datastore, durable_prefix="demo-test", pull_timeout=0.01)

    stored = await archiver.drain(batch_size=telemetry_count + 10)
    expected_total = telemetry_count + 2  # command + tag
    assert stored == expected_total
    assert await datastore.count_messages() == expected_total
    assert await datastore.count_commands() == 1
    assert await datastore.count_tags() == 1

    latest_command = await datastore.latest_command(command.name)
    assert latest_command is not None
    assert latest_command["payload"]["units"] == "metric"

    stored_tag = await datastore.get_tag("tag-1")
    assert stored_tag is not None
    assert stored_tag.label == "Integration Test"

    replayer = StoreReplayer(datastore, jetstream, sleep=lambda _: asyncio.sleep(0))
    all_records = await datastore.fetch_messages_between(float("-inf"), float("inf"))
    assert len(all_records) == expected_total
    replay_window_start = min(record.published_ts for record in all_records) - 1.0
    replay_window_end = max(record.published_ts for record in all_records) + 1.0
    replayed = await replayer.replay_time_window(
        "demo", replay_window_start, replay_window_end, pace=False
    )
    assert len(replayed) == expected_total

    consumer = jetstream.create_consumer("player.demo.>")
    replay_messages = consumer.pull(expected_total)
    assert len(replay_messages) == expected_total
    assert all(message.subject.startswith("player.demo.playout.") for message in replay_messages)


def test_demo_like_integration() -> None:
    """Exercise generator → JetStream → archiver → datastore → replay flow."""

    asyncio.run(_run_demo_like_flow())
