"""Tests for parsing TSPI datagrams."""

import struct

import pytest

from tspi_kit.datagrams import ParsedTSPI, parse_tspi_datagram
from tspi_kit.jetstream import build_subject, message_headers
from tspi_kit.schema import validate_payload

_HEADER_FORMAT = ">BBHHIBH"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)


def _build_header(
    *,
    message_type: int,
    sensor_id: int,
    day: int,
    time_ticks: int,
    status: int,
    status_flags: int,
) -> bytes:
    return struct.pack(
        _HEADER_FORMAT,
        message_type,
        4,  # BAPS TSPI v4
        sensor_id,
        day,
        time_ticks,
        status,
        status_flags,
    )


def _combine(header: bytes, payload: bytes) -> bytes:
    datagram = header + payload
    assert len(datagram) == 37
    return datagram


def _validate(parsed: ParsedTSPI) -> None:
    payload = {
        "type": parsed.type,
        "sensor_id": parsed.sensor_id,
        "day": parsed.day,
        "time_s": parsed.time_s,
        "status": parsed.status,
        "status_flags": parsed.status_flags,
        "recv_epoch_ms": 1_700_000_000_000,
        "recv_iso": "2024-03-20T00:00:00+00:00",
        "payload": parsed.payload,
    }
    validate_payload(payload)


def test_parse_geocentric_datagram() -> None:
    header = _build_header(
        message_type=0xC1,
        sensor_id=501,
        day=123,
        time_ticks=15340,
        status=0xFF,
        status_flags=0b0000_0000_0000_0001,
    )
    payload = struct.pack(
        ">iii hhh hhh".replace(" ", ""),
        round(5123.25 * 100),
        round(-15.5 * 100),
        round(1200.0 * 100),
        round(12.34 * 100),
        round(-5.67 * 100),
        round(0.0 * 100),
        round(0.12 * 100),
        round(-0.34 * 100),
        round(0.56 * 100),
    )

    datagram = _combine(header, payload)
    parsed = parse_tspi_datagram(datagram)

    assert parsed.type == "geocentric"
    assert parsed.sensor_id == 501
    assert parsed.day == 123
    assert pytest.approx(parsed.time_s, rel=1e-9) == 1.534
    assert parsed.status == 0xFF
    assert parsed.status_flags == {
        "position_x_valid": True,
        "position_y_valid": True,
        "position_z_valid": True,
        "velocity_x_valid": True,
        "velocity_y_valid": True,
        "velocity_z_valid": True,
        "acceleration_x_valid": True,
        "acceleration_y_valid": True,
        "acceleration_z_valid": True,
    }
    assert parsed.payload == {
        "x_m": pytest.approx(5123.25),
        "y_m": pytest.approx(-15.5),
        "z_m": pytest.approx(1200.0),
        "vx_mps": pytest.approx(12.34),
        "vy_mps": pytest.approx(-5.67),
        "vz_mps": pytest.approx(0.0),
        "ax_mps2": pytest.approx(0.12),
        "ay_mps2": pytest.approx(-0.34),
        "az_mps2": pytest.approx(0.56),
    }
    assert parsed.deduplication_id() == "501:123:15340"
    assert build_subject(parsed) == "tspi.geocentric.501"
    assert message_headers(parsed) == {"Nats-Msg-Id": "501:123:15340"}

    _validate(parsed)


def test_parse_spherical_datagram() -> None:
    header = _build_header(
        message_type=0xC2,
        sensor_id=2048,
        day=42,
        time_ticks=923400,
        status=0b0000_1110,
        status_flags=0b0000_0000_0000_0111,
    )
    payload = struct.pack(
        ">iII hhh hhh".replace(" ", ""),
        round(3800.0 * 100),
        round(52.123456 * 1_000_000),
        round(10.654321 * 1_000_000),
        round(1.23 * 100),
        round(-4.56 * 100),
        round(7.89 * 100),
        round(0.12 * 100),
        round(-0.34 * 100),
        round(0.56 * 100),
    )

    datagram = _combine(header, payload)
    parsed = parse_tspi_datagram(datagram)

    assert parsed.type == "spherical"
    assert parsed.sensor_id == 2048
    assert parsed.day == 42
    assert pytest.approx(parsed.time_s, rel=1e-9) == 92.34
    assert parsed.status_flags["position_x_valid"] is False
    assert parsed.status_flags["velocity_x_valid"] is True
    assert parsed.payload == {
        "range_m": pytest.approx(3800.0),
        "azimuth_deg": pytest.approx(52.123456),
        "elevation_deg": pytest.approx(10.654321),
        "azimuth_rate_dps": pytest.approx(1.23),
        "elevation_rate_dps": pytest.approx(-4.56),
        "range_rate_mps": pytest.approx(7.89),
        "azimuth_accel_dps2": pytest.approx(0.12),
        "elevation_accel_dps2": pytest.approx(-0.34),
        "range_accel_mps2": pytest.approx(0.56),
    }
    assert parsed.deduplication_id() == "2048:42:923400"
    assert build_subject(parsed, stream_prefix="custom") == "custom.spherical.2048"

    _validate(parsed)


def test_invalid_length() -> None:
    header = _build_header(
        message_type=0xC1,
        sensor_id=1,
        day=1,
        time_ticks=0,
        status=0,
        status_flags=0,
    )
    payload = struct.pack(">iii hhh hhh".replace(" ", ""), *([0] * 9))

    datagram = header + payload + b"extra"
    with pytest.raises(ValueError):
        parse_tspi_datagram(datagram)


def test_invalid_version() -> None:
    header = struct.pack(
        _HEADER_FORMAT,
        0xC1,
        3,  # unsupported version
        1,
        1,
        0,
        0,
        0,
    )
    payload = struct.pack(">iii hhh hhh".replace(" ", ""), *([0] * 9))
    datagram = header + payload

    with pytest.raises(ValueError):
        parse_tspi_datagram(datagram)
