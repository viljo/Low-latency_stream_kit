import cbor2

from tspi_kit.commands import (
    COMMAND_SUBJECT_PREFIX,
    OPS_CONTROL_SUBJECT,
    CommandSender,
    OpsControlSender,
)


class _StubPublisher:
    def __init__(self) -> None:
        self.messages = []

    def publish(self, subject, data, headers=None, timestamp=None):
        self.messages.append((subject, data, headers))
        return True


def test_send_session_metadata_broadcasts_payload() -> None:
    publisher = _StubPublisher()
    sender = CommandSender(publisher, sender_id="console")

    payload = sender.send_session_metadata("Falcon Lead", "42")

    assert publisher.messages
    subject, data, headers = publisher.messages[-1]
    assert subject == f"{COMMAND_SUBJECT_PREFIX}.session_metadata"
    decoded = cbor2.loads(data)
    assert decoded["name"] == "display.session_metadata"
    assert decoded["payload"]["session_metadata"] == {"name": "Falcon Lead", "id": "42"}
    assert payload.payload["session_metadata"]["name"] == "Falcon Lead"
    assert payload.payload["session_metadata"]["id"] == "42"


def test_send_session_metadata_requires_both_fields() -> None:
    sender = CommandSender(_StubPublisher())

    try:
        sender.send_session_metadata("", "42")
    except ValueError as exc:
        assert "Session name" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected ValueError for empty tag name")

    try:
        sender.send_session_metadata("Falcon", " ")
    except ValueError as exc:
        assert "Session identifier" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected ValueError for empty session identifier")


def test_ops_sender_starts_group_replay() -> None:
    publisher = _StubPublisher()
    ops = OpsControlSender(publisher, sender_id="console")

    message = ops.start_group_replay("2025-09-28T11:00:00Z")

    assert publisher.messages
    subject, data, headers = publisher.messages[-1]
    assert subject == OPS_CONTROL_SUBJECT
    decoded = cbor2.loads(data)
    assert decoded["type"] == "GroupReplayStart"
    assert decoded["channel_id"] == message.channel.channel_id
    assert decoded["identifier"] == "2025-09-28T11:00:00Z"
    assert headers["X-Command-Sender"] == "console"


def test_ops_sender_stops_last_group_replay() -> None:
    publisher = _StubPublisher()
    ops = OpsControlSender(publisher)

    start_message = ops.start_group_replay("2025-09-28T11:00:00Z")
    stop_message = ops.stop_group_replay()

    assert start_message.channel.channel_id == stop_message.channel_id
    assert publisher.messages[-1][0] == OPS_CONTROL_SUBJECT
    decoded = cbor2.loads(publisher.messages[-1][1])
    assert decoded["type"] == "GroupReplayStop"
    assert decoded["channel_id"] == start_message.channel.channel_id


def test_ops_sender_requires_channel_id_when_unknown() -> None:
    ops = OpsControlSender(_StubPublisher())

    try:
        ops.stop_group_replay()
    except ValueError as exc:
        assert "channel_id" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected ValueError when stopping without a known channel")
