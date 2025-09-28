"""Threaded JetStream client adapters for synchronous UI components."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from nats import errors as nats_errors
from nats.aio.client import Client as NATS

ErrNoServers = getattr(nats_errors, "ErrNoServers", nats_errors.NoServersError)
TimeoutError = getattr(
    nats_errors, "TimeoutError", getattr(nats_errors, "ErrTimeout", asyncio.TimeoutError)
)


def normalize_stream_subjects(subjects: Sequence[str]) -> List[str]:
    """Remove redundant subjects that are already covered by broader wildcards."""

    entries: List[tuple[str, tuple[str, ...], tuple[str, ...] | None]] = []
    for subject in subjects:
        tokens = tuple(subject.split("."))
        tail_prefix: tuple[str, ...] | None = None
        if tokens and tokens[-1] == ">":
            tail_prefix = tokens[:-1]
        entries.append((subject, tokens, tail_prefix))

    normalized: List[str] = []
    for index, (subject, tokens, _tail_prefix) in enumerate(entries):
        covered = False
        for other_index, (_, _, other_tail_prefix) in enumerate(entries):
            if index == other_index:
                continue
            if other_tail_prefix is None:
                continue
            if len(tokens) < len(other_tail_prefix):
                continue
            if tokens[: len(other_tail_prefix)] == other_tail_prefix:
                covered = True
                break
        if not covered:
            normalized.append(subject)
    return normalized

from nats.js.errors import NotFoundError


class JetStreamPublisherAdapter:
    """Adapter exposing a synchronous ``publish`` API backed by JetStream."""

    def __init__(self, loop: asyncio.AbstractEventLoop, js_context) -> None:
        self._loop = loop
        self._js = js_context

    def publish(self, subject: str, payload: bytes, *, headers=None, timestamp=None) -> bool:
        coro = self._js.publish(subject, payload, headers=headers)
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            future.result(timeout=5)
            return True
        except Exception:  # pragma: no cover - surfaced to caller
            return False


@dataclass
class _AckingMessage:
    """Simple container matching the API expected by :class:`TSPIReceiver`."""

    data: bytes


class JetStreamConsumerAdapter:
    """Expose JetStream pull subscriptions via a synchronous ``pull`` API."""

    def __init__(self, loop: asyncio.AbstractEventLoop, subscription) -> None:
        self._loop = loop
        self._subscription = subscription

    def pull(self, batch: int) -> List[_AckingMessage]:
        coro = self._subscription.fetch(batch, timeout=1)
        future: Future[Sequence] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            messages = future.result(timeout=5)
        except TimeoutError:
            return []
        except Exception:  # pragma: no cover - surfaced to caller
            return []

        results: List[_AckingMessage] = []
        for message in messages:
            results.append(_AckingMessage(data=message.data))
            ack_future = asyncio.run_coroutine_threadsafe(message.ack(), self._loop)
            try:
                ack_future.result(timeout=5)
            except Exception:  # pragma: no cover - diagnostics only
                pass
        return results

    def pending(self) -> int:
        coro = self._subscription.consumer_info()
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            info = future.result(timeout=5)
            return getattr(info, "num_pending", 0)
        except Exception:
            return 0


class JetStreamThreadedClient:
    """Run a JetStream client on a background thread for synchronous callers."""

    def __init__(
        self,
        servers: Iterable[str],
        *,
        connect_timeout: float = 2.0,
        reconnect_time_wait: float = 0.5,
    ) -> None:
        self._servers = list(servers)
        if not self._servers:
            raise ValueError("At least one NATS server must be provided")
        self._connect_timeout = connect_timeout
        self._reconnect_time_wait = reconnect_time_wait
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="jetstream-client", daemon=True)
        self._nc: Optional[NATS] = None
        self._js = None
        self._started = False

    # ------------------------------------------------------------------
    # Background event loop lifecycle
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self) -> None:
        if self._started:
            return
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        future.result(timeout=60)
        self._started = True

    async def _connect(self) -> None:
        deadline = self._loop.time() + 60.0
        attempt = 0
        last_error: Exception | None = None
        while True:
            attempt += 1
            self._nc = NATS()
            try:
                await self._nc.connect(
                    servers=self._servers,
                    connect_timeout=self._connect_timeout,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=self._reconnect_time_wait,
                )
                self._js = self._nc.jetstream()
                return
            except ErrNoServers as exc:
                last_error = exc
                if self._loop.time() >= deadline:
                    raise RuntimeError("Timed out connecting to NATS JetStream") from exc
                await asyncio.sleep(1.0)

        if last_error is not None:  # pragma: no cover - defensive
            raise last_error

    def close(self) -> None:
        if not self._started:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._started = False

    async def _shutdown(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            await self._nc.drain()
        if self._nc is not None:
            await self._nc.close()

    # ------------------------------------------------------------------
    # JetStream helpers
    # ------------------------------------------------------------------
    def ensure_stream(self, name: str, subjects: Sequence[str], *, num_replicas: int | None = None) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._ensure_stream(name, subjects, num_replicas=num_replicas),
            self._loop,
        )
        future.result(timeout=10)

    async def _ensure_stream(
        self, name: str, subjects: Sequence[str], *, num_replicas: int | None
    ) -> None:
        assert self._js is not None
        try:
            await self._js.stream_info(name)
            return
        except NotFoundError:
            pass
        config = {"name": name, "subjects": normalize_stream_subjects(list(subjects))}
        if num_replicas is not None:
            config["num_replicas"] = num_replicas
        await self._js.add_stream(**config)

    def publisher(self) -> JetStreamPublisherAdapter:
        if self._js is None:
            raise RuntimeError("JetStream client not started")
        return JetStreamPublisherAdapter(self._loop, self._js)

    def create_pull_consumer(
        self,
        subject: str,
        *,
        durable: str | None = None,
        stream: str | None = None,
    ) -> JetStreamConsumerAdapter:
        if self._js is None:
            raise RuntimeError("JetStream client not started")
        future = asyncio.run_coroutine_threadsafe(
            self._js.pull_subscribe(subject, durable=durable, stream=stream),
            self._loop,
        )
        subscription = future.result(timeout=10)
        return JetStreamConsumerAdapter(self._loop, subscription)


__all__ = [
    "JetStreamConsumerAdapter",
    "JetStreamPublisherAdapter",
    "JetStreamThreadedClient",
    "normalize_stream_subjects",
]
