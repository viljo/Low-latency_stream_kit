from datetime import UTC, datetime

import pytest

from tspi_kit.tags import TAG_BROADCAST_SUBJECT, TagSender


class _RecordingPublisher:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.published = []

    def publish(self, subject: str, payload: bytes, *, headers=None, timestamp=None):  # noqa: D401 - signature matches callers
        self.published.append((subject, payload, headers or {}, timestamp))
        return self.result


def test_tag_sender_publishes_comment_with_timestamp() -> None:
    publisher = _RecordingPublisher()
    sender = TagSender(publisher, sender_id="ops")
    timestamp = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    payload = sender.create_tag("  Intercept start  ", timestamp=timestamp)

    assert payload.label == "Intercept start"
    assert payload.notes == "Intercept start"
    assert payload.ts.startswith("2025-01-01T12:00:00")
    assert payload.creator == "ops"
    assert publisher.published
    subject, raw, headers, ts = publisher.published[-1]
    assert subject == TAG_BROADCAST_SUBJECT
    assert headers["Nats-Msg-Id"] == payload.id
    assert headers["X-Tag-Creator"] == "ops"
    assert raw  # ensure non-empty CBOR payload
    assert isinstance(ts, float)


def test_tag_sender_rejects_empty_comment() -> None:
    sender = TagSender(_RecordingPublisher())
    with pytest.raises(ValueError):
        sender.create_tag("   ")


def test_tag_sender_raises_when_publish_fails() -> None:
    sender = TagSender(_RecordingPublisher(result=False))
    with pytest.raises(RuntimeError):
        sender.create_tag("Broken")
