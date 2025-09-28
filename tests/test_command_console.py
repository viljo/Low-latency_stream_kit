from datetime import UTC, datetime, timedelta

import pytest

from tspi_kit.ui.command_console import (
    ClientPresenceTracker,
    DataChunk,
    DataChunkTag,
    compose_replay_identifier,
)


def _ts(text: str) -> str:
    return text


def test_tracker_emits_connection_event() -> None:
    tracker = ClientPresenceTracker()
    presence, events = tracker.process_payload(
        {
            "client_id": "alpha-01",
            "state": "FOLLOWING_LIVESTREAM",
            "channel_id": "livestream",
            "ts": _ts("2025-01-01T00:00:00Z"),
            "source_ip": "203.0.113.12",
            "operator": "ops",
            "ping_ms": 12.5,
        }
    )
    assert presence is not None
    assert presence.client_id == "alpha-01"
    assert presence.channel_id == "livestream"
    assert presence.state_display == "Following Livestream"
    assert presence.operator == "ops"
    assert presence.source_ip == "203.0.113.12"
    assert presence.connection_ts == datetime(2025, 1, 1, tzinfo=UTC)
    assert len(events) == 1
    assert "New client connected" in events[0].message


def test_tracker_reports_replay_and_live_transitions() -> None:
    tracker = ClientPresenceTracker()
    tracker.process_payload(
        {
            "client_id": "bravo-02",
            "state": "FOLLOWING_LIVESTREAM",
            "channel_id": "livestream",
            "ts": _ts("2025-02-15T21:40:00Z"),
        }
    )

    _, replay_events = tracker.process_payload(
        {
            "client_id": "bravo-02",
            "state": "FOLLOWING_GROUP_REPLAY",
            "channel_id": "replay.20250215T214500Z",
            "ts": _ts("2025-02-15T21:45:00Z"),
        }
    )
    assert replay_events
    assert any("started replay" in event.message for event in replay_events)

    _, live_events = tracker.process_payload(
        {
            "client_id": "bravo-02",
            "state": "FOLLOWING_LIVESTREAM",
            "channel_id": "livestream",
            "ts": _ts("2025-02-15T21:55:00Z"),
        }
    )
    assert live_events
    assert any("resumed live view" in event.message for event in live_events)


def test_tracker_highlights_live_override() -> None:
    tracker = ClientPresenceTracker()
    tracker.process_payload(
        {
            "client_id": "charlie-03",
            "state": "FOLLOWING_GROUP_REPLAY",
            "channel_id": "replay.20250301T180000Z",
            "ts": _ts("2025-03-01T18:00:00Z"),
        }
    )

    _, events = tracker.process_payload(
        {
            "client_id": "charlie-03",
            "state": "LIVE_OVERRIDE",
            "channel_id": "livestream",
            "ts": _ts("2025-03-01T18:03:00Z"),
            "override": True,
        }
    )
    assert events
    assert any("initiated live override" in event.message for event in events)


def test_datachunk_offsets_and_identifier_selection() -> None:
    start = datetime(2025, 5, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    tag = DataChunkTag("Intercept Start", start + timedelta(minutes=5))
    chunk = DataChunk(
        "chunk-01",
        start,
        end,
        display_name="Intercept Window",
        tags=(tag,),
    )

    assert chunk.duration_seconds == 1800
    assert chunk.offset_for_timestamp(start + timedelta(minutes=10)) == 600
    assert chunk.timestamp_at_offset(600) == start + timedelta(minutes=10)

    identifier, display = compose_replay_identifier(chunk, start_time=start + timedelta(minutes=12))
    assert "chunk-01" in identifier
    assert "2025-05-01T12:12:00Z" in identifier
    assert "@" in display

    tag_identifier, tag_display = compose_replay_identifier(chunk, start_time=tag.timestamp, tag=tag)
    assert "Intercept Start" in tag_identifier
    assert "Intercept Start" in tag_display


def test_datachunk_rejects_empty_identifier() -> None:
    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        DataChunk("", start, start + timedelta(hours=1))
