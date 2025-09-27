"""In-memory JetStream simulation utilities for integration tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class JetStreamMessage:
    """Container representing a message stored in the in-memory JetStream."""

    subject: str
    data: bytes
    headers: Dict[str, str]
    timestamp: float


def _match_token(subject_token: str, pattern_token: str) -> bool:
    if pattern_token == ">":
        return True
    if pattern_token == "*":
        return True
    return subject_token == pattern_token


def _match_subject(subject: str, pattern: str) -> bool:
    if pattern == ">":
        return True

    subject_tokens = subject.split(".")
    pattern_tokens = pattern.split(".")

    for index, token in enumerate(pattern_tokens):
        if token == ">":
            return True
        if index >= len(subject_tokens):
            return False
        if not _match_token(subject_tokens[index], token):
            return False

    return len(subject_tokens) == len(pattern_tokens)


class InMemoryConsumer:
    """Simple pull consumer used for integration tests."""

    def __init__(self, stream: "InMemoryJetStream", subject_filter: str):
        self._stream = stream
        self._subject_filter = subject_filter
        self._cursor = 0
        self._delivered = 0

    def pull(self, batch: int) -> List[JetStreamMessage]:
        messages: List[JetStreamMessage] = []

        while len(messages) < batch and self._cursor < len(self._stream._messages):
            message = self._stream._messages[self._cursor]
            self._cursor += 1
            if _match_subject(message.subject, self._subject_filter):
                messages.append(message)

        self._delivered += len(messages)
        return messages

    def pending(self) -> int:
        matched = [
            message
            for message in self._stream._messages
            if _match_subject(message.subject, self._subject_filter)
        ]
        return max(len(matched) - self._delivered, 0)


class InMemoryJetStream:
    """Minimal in-memory JetStream simulation supporting deduplication."""

    def __init__(self) -> None:
        self._messages: List[JetStreamMessage] = []
        self._deduplication: Dict[str, JetStreamMessage] = {}

    def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Optional[Dict[str, str]] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        headers = headers or {}
        timestamp = timestamp if timestamp is not None else 0.0
        message_id = headers.get("Nats-Msg-Id")
        if message_id is not None and message_id in self._deduplication:
            return False

        message = JetStreamMessage(subject, payload, headers, timestamp)
        self._messages.append(message)
        if message_id is not None:
            self._deduplication[message_id] = message
        return True

    def create_consumer(self, subject_filter: str) -> InMemoryConsumer:
        return InMemoryConsumer(self, subject_filter)


class InMemoryJetStreamCluster(InMemoryJetStream):
    """In-memory JetStream cluster with simple leader failover semantics."""

    def __init__(self, replicas: int = 3) -> None:
        super().__init__()
        if replicas < 1:
            raise ValueError("Cluster must contain at least one replica")
        self._replicas = replicas
        self._leader_index = 0
        self._alive = [True] * replicas

    @property
    def leader_index(self) -> int:
        return self._leader_index

    def kill_leader(self) -> None:
        self._alive[self._leader_index] = False
        self._leader_index = self._next_leader()

    def revive_all(self) -> None:
        self._alive = [True] * self._replicas
        self._leader_index = 0

    def _next_leader(self) -> int:
        for index, alive in enumerate(self._alive):
            if alive:
                return index
        raise RuntimeError("No replicas available for leadership")

    def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Optional[Dict[str, str]] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        if not self._alive[self._leader_index]:
            self._leader_index = self._next_leader()
        return super().publish(subject, payload, headers=headers, timestamp=timestamp)
