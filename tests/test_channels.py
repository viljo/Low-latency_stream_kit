"""Tests covering the channel and replay specification helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from tspi_kit.channels import (
    ChannelDirectory,
    ChannelKind,
    ChannelManager,
    ChannelStatus,
    ClientState,
    GroupReplayStartMessage,
    GroupReplayStopMessage,
    live_channel,
    group_replay_channel,
    private_channel,
    live_consumer_config,
    replay_consumer_config,
    replay_advertisement_subjects,
)
from tspi_kit.datastore import TimescaleDatastore


def test_live_channel_descriptor() -> None:
    channel = live_channel()
    assert channel.channel_id == "livestream"
    assert channel.subject == "tspi.channel.livestream"
    assert channel.display_name == "livestream"
    assert channel.kind is ChannelKind.LIVESTREAM


def test_group_replay_channel_descriptor_roundtrip() -> None:
    ts = datetime(2025, 9, 28, 11, 0, 0, tzinfo=timezone.utc)
    channel = group_replay_channel(ts)
    assert channel.channel_id == "replay.20250928T110000Z"
    assert channel.subject == "tspi.channel.replay.20250928T110000Z"
    assert channel.display_name == "replay 2025-09-28T11:00:00Z"
    assert channel.identifier == "2025-09-28T11:00:00Z"


def test_private_channel_descriptor_validation() -> None:
    channel = private_channel("viljo", "3f19")
    assert channel.channel_id == "client.viljo.3f19"
    assert channel.subject == "tspi.channel.client.viljo.3f19"
    assert channel.display_name == "client viljo/3f19"
    assert channel.kind is ChannelKind.PRIVATE_REPLAY

    with pytest.raises(ValueError):
        private_channel("", "session")


def test_channel_directory_includes_livestream_and_filters_privates() -> None:
    directory = ChannelDirectory()
    replay = group_replay_channel("2025-09-28T11:00:00Z")
    directory.upsert(replay)
    private = private_channel("alice", "abc123")
    directory.upsert(private)

    all_channels = directory.list_channels()
    assert [c.channel_id for c in all_channels] == [
        "livestream",
        replay.channel_id,
        private.channel_id,
    ]

    public_only = directory.list_channels(include_private=False)
    assert [c.channel_id for c in public_only] == ["livestream", replay.channel_id]


def test_channel_manager_controls_group_replay_lifecycle() -> None:
    manager = ChannelManager()
    start_msg = manager.start_group_replay("2025-09-28T11:00:00Z")
    assert isinstance(start_msg, GroupReplayStartMessage)
    active = manager.directory.list_channels()[1]
    assert active.channel_id == "replay.20250928T110000Z"

    stop_msg = manager.stop_group_replay()
    assert isinstance(stop_msg, GroupReplayStopMessage)
    assert stop_msg.channel_id == active.channel_id
    assert len(manager.directory.list_channels()) == 1


def test_channel_status_serialises_to_dict() -> None:
    channel = live_channel()
    status = ChannelStatus(
        client_id="alice",
        state=ClientState.FOLLOWING_LIVESTREAM,
        channel=channel,
        override=False,
        timestamp="2025-09-28T11:00:00Z",
    )
    payload = status.to_dict()
    assert payload["client_id"] == "alice"
    assert payload["state"] == "FOLLOWING_LIVESTREAM"
    assert payload["channel_id"] == "livestream"
    assert payload["subject"] == channel.subject
    assert payload["override"] is False
    assert payload["ts"] == "2025-09-28T11:00:00Z"


def test_consumer_config_helpers() -> None:
    live_config = live_consumer_config()
    assert live_config["deliver_subject"] == "tspi.channel.livestream"
    replay = group_replay_channel("2025-09-28T11:00:00Z")
    replay_config = replay_consumer_config(replay)
    assert replay_config["deliver_subject"] == replay.subject
    assert replay_config["replay_policy"] == "original"
    assert replay_config["description"].startswith("Group replay")
    assert replay_config["deliver_policy"] == "by_start_time"


def test_replay_consumer_config_for_tag_identifier() -> None:
    replay = group_replay_channel("Intercept Window 3")
    replay_config = replay_consumer_config(replay)
    assert replay_config["deliver_policy"] == "deliver_new"


def test_replay_advertisement_subjects_match_spec() -> None:
    subjects = replay_advertisement_subjects()
    assert "tspi.channel.replay.>" in subjects
    assert "tspi.channel.client.>" in subjects


def test_datastore_skips_non_livestream_channel_messages() -> None:
    datastore = TimescaleDatastore("postgresql://example")

    async def _call() -> int | None:
        return await datastore.insert_message(
            subject="tspi.channel.replay.20250928T110000Z",
            kind="telemetry",
            payload={},
            headers={},
            published_ts="2025-09-28T11:00:00Z",
            raw_cbor=b"",
        )

    result = asyncio.run(_call())
    assert result is None
