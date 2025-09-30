"""Integration test exercising a demo-like end-to-end workflow."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, List, Optional, Sequence

import cbor2

from nats.js.errors import BadRequestError, NotFoundError

from tspi_kit import (
    Archiver,
    CommandSender,
    FlightConfig,
    InMemoryJetStream,
    StoreReplayer,
    TSPIFlightGenerator,
    TSPIProducer,
)
from tspi_kit.commands import COMMAND_SUBJECT_PREFIX
from tspi_kit.jetstream_client import normalize_stream_subjects
from tspi_kit.jetstream_sim import JetStreamMessage, _match_subject

_DEMO_PATH = Path(__file__).resolve().parents[1] / "demo"
_DEMO_LOADER = importlib.machinery.SourceFileLoader("_demo_module", str(_DEMO_PATH))
_DEMO_SPEC = importlib.util.spec_from_loader("_demo_module", _DEMO_LOADER)
if _DEMO_SPEC is None:  # pragma: no cover - defensive import guard
    raise RuntimeError("Unable to load demo helper module for integration test")
_DEMO_MODULE = importlib.util.module_from_spec(_DEMO_SPEC)
_DEMO_LOADER.exec_module(_DEMO_MODULE)
TimescaleHACluster = _DEMO_MODULE.TimescaleHACluster


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


class _RestartableDemoStream:
    """Stub JetStream context emulating restart behaviour for regression tests."""

    def __init__(self) -> None:
        self._subjects: List[str] | None = None
        self._replicas = 1
        self.fail_on_mismatch = False
        self.update_calls: List[dict[str, object]] = []
        self.delete_calls = 0
        self.messages: List[tuple[str, bytes]] = []
        self.deleted_consumers: List[tuple[str, str]] = []

    async def stream_info(self, name: str):
        if self._subjects is None:
            raise NotFoundError()
        config = SimpleNamespace(subjects=list(self._subjects))
        return SimpleNamespace(config=config, cluster=None)

    async def add_stream(
        self,
        *,
        name: str,
        subjects: Sequence[str],
        num_replicas: int,
        retention: str,
        max_msgs: int,
        max_bytes: int,
    ) -> None:  # noqa: ARG002 - parity with real client
        self._subjects = list(subjects)
        self._replicas = num_replicas

    async def update_stream(self, *args, **kwargs) -> None:
        assert not args, "update_stream should receive keyword arguments"
        if not kwargs:
            raise AssertionError("update_stream requires keyword arguments")
        self.update_calls.append(dict(kwargs))
        subjects = kwargs.get("subjects")
        if subjects is None:
            raise AssertionError("subjects keyword argument missing")
        normalized_existing = normalize_stream_subjects(self._subjects or [])
        normalized_new = normalize_stream_subjects(subjects)
        if self.fail_on_mismatch and normalized_existing != normalized_new:
            raise BadRequestError(description="stream config conflict", err_code=50076)
        self._subjects = list(subjects)
        self._replicas = int(kwargs.get("num_replicas", self._replicas))

    async def delete_stream(self, name: str) -> None:
        self.delete_calls += 1
        self._subjects = None

    async def delete_consumer(self, stream: str, durable: str) -> None:  # noqa: ARG002
        self.deleted_consumers.append((stream, durable))
        raise NotFoundError()

    def publish(self, subject: str, payload: bytes) -> bool:
        if self._subjects is None:
            raise RuntimeError("Stream is not configured")
        if not any(_match_subject(subject, candidate) for candidate in self._subjects):
            raise ValueError(f"Subject {subject!r} rejected by stream configuration")
        self.messages.append((subject, payload))
        return True


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

    datastore = TimescaleHACluster(replicas=2)
    archiver = Archiver(jetstream, datastore, durable_prefix="demo-test", pull_timeout=0.01)

    stored = await archiver.drain(batch_size=telemetry_count + 10)
    expected_total = telemetry_count + 2  # command + tag
    assert stored == expected_total
    assert await datastore.count_messages() == expected_total
    assert await datastore.count_commands() == 1
    assert await datastore.count_tags() == 1

    replica_totals = await datastore.replica_message_counts()
    assert replica_totals == [expected_total] * datastore.replica_count

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


async def _run_demo_restart_flow() -> None:
    stream = _RestartableDemoStream()
    await stream.add_stream(
        name="TSPI",
        subjects=["tspi.>"],
        num_replicas=1,
        retention="limits",
        max_msgs=-1,
        max_bytes=-1,
    )
    stream.fail_on_mismatch = True

    await _DEMO_MODULE.prepare_stream(
        stream,
        1,
        NotFoundError=NotFoundError,
        BadRequestError=BadRequestError,
        timeout_exceptions=(asyncio.TimeoutError,),
    )

    stream.fail_on_mismatch = False
    await _DEMO_MODULE.prepare_stream(
        stream,
        1,
        NotFoundError=NotFoundError,
        BadRequestError=BadRequestError,
        timeout_exceptions=(asyncio.TimeoutError,),
    )

    expected_subjects = normalize_stream_subjects(
        ["tspi.>", f"{COMMAND_SUBJECT_PREFIX}.>", "tags.>"]
    )
    assert normalize_stream_subjects(stream._subjects or []) == expected_subjects
    assert stream.delete_calls >= 1
    assert stream.update_calls, "prepare_stream should invoke update_stream"
    assert all("name" in call and "subjects" in call for call in stream.update_calls)

    assert stream.publish("tspi.demo.flight", b"telemetry")
    assert stream.publish("tags.demo.created", b"tag")
    assert stream.messages[-2:] == [
        ("tspi.demo.flight", b"telemetry"),
        ("tags.demo.created", b"tag"),
    ]


def test_demo_restart_allows_second_run(tmp_path) -> None:  # noqa: ARG001
    """Regression test for restarting the demo with an existing store directory."""

    asyncio.run(_run_demo_restart_flow())
