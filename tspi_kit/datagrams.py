"""Binary datagram parsing utilities for BAPS TSPI telemetry."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Literal
import struct

_DATAGRAM_LENGTH = 37
_HEADER_FORMAT = ">BBHHIBH"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)
_PAYLOAD_SIZE = _DATAGRAM_LENGTH - _HEADER_SIZE

_GEOCENTRIC_FORMAT = ">iii hhh hhh".replace(" ", "")
_SPHERICAL_FORMAT = ">iII hhh hhh".replace(" ", "")


class MessageType(Enum):
    """Enumeration of supported TSPI datagram types."""

    GEOCENTRIC = 0xC1
    SPHERICAL = 0xC2

    @classmethod
    def from_byte(cls, value: int) -> "MessageType":
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"Unsupported message type byte: 0x{value:02x}") from exc


@dataclass(frozen=True)
class ParsedTSPI:
    """Canonical representation of a TSPI datagram."""

    type: Literal["geocentric", "spherical"]
    sensor_id: int
    day: int
    time_s: float
    time_ticks: int
    status: int
    status_flags: Dict[str, bool]
    payload: Dict[str, float]
    version: int = 4

    def deduplication_id(self) -> str:
        """Return a JetStream de-duplication identifier."""

        return f"{self.sensor_id}:{self.day}:{self.time_ticks}"


def _unpack_header(datagram: bytes) -> tuple[MessageType, int, int, int, int, int, int]:
    if len(datagram) != _DATAGRAM_LENGTH:
        raise ValueError(
            f"TSPI datagram must be exactly {_DATAGRAM_LENGTH} bytes; received {len(datagram)}"
        )

    (
        message_type_byte,
        version,
        sensor_id,
        day,
        time_ticks,
        status,
        status_flags,
    ) = struct.unpack(_HEADER_FORMAT, datagram[:_HEADER_SIZE])

    message_type = MessageType.from_byte(message_type_byte)
    if version != 4:
        raise ValueError(f"Unsupported datagram version: {version}")

    return message_type, version, sensor_id, day, time_ticks, status, status_flags


def _status_bits(status: int, status_flags: int) -> Dict[str, bool]:
    combined = status | (status_flags << 8)
    labels = [
        "position_x_valid",
        "position_y_valid",
        "position_z_valid",
        "velocity_x_valid",
        "velocity_y_valid",
        "velocity_z_valid",
        "acceleration_x_valid",
        "acceleration_y_valid",
        "acceleration_z_valid",
    ]
    return {label: bool(combined & (1 << index)) for index, label in enumerate(labels)}


def _parse_geocentric(payload: bytes) -> Dict[str, float]:
    if len(payload) != _PAYLOAD_SIZE:
        raise ValueError("Invalid geocentric payload length")

    (
        x_raw,
        y_raw,
        z_raw,
        vx_raw,
        vy_raw,
        vz_raw,
        ax_raw,
        ay_raw,
        az_raw,
    ) = struct.unpack(_GEOCENTRIC_FORMAT, payload)

    scale = 100.0
    return {
        "x_m": x_raw / scale,
        "y_m": y_raw / scale,
        "z_m": z_raw / scale,
        "vx_mps": vx_raw / scale,
        "vy_mps": vy_raw / scale,
        "vz_mps": vz_raw / scale,
        "ax_mps2": ax_raw / scale,
        "ay_mps2": ay_raw / scale,
        "az_mps2": az_raw / scale,
    }


def _parse_spherical(payload: bytes) -> Dict[str, float]:
    if len(payload) != _PAYLOAD_SIZE:
        raise ValueError("Invalid spherical payload length")

    (
        range_raw,
        azimuth_raw,
        elevation_raw,
        az_rate_raw,
        el_rate_raw,
        range_rate_raw,
        az_accel_raw,
        el_accel_raw,
        range_accel_raw,
    ) = struct.unpack(_SPHERICAL_FORMAT, payload)

    return {
        "range_m": range_raw / 100.0,
        "azimuth_deg": azimuth_raw / 1_000_000.0,
        "elevation_deg": elevation_raw / 1_000_000.0,
        "azimuth_rate_dps": az_rate_raw / 100.0,
        "elevation_rate_dps": el_rate_raw / 100.0,
        "range_rate_mps": range_rate_raw / 100.0,
        "azimuth_accel_dps2": az_accel_raw / 100.0,
        "elevation_accel_dps2": el_accel_raw / 100.0,
        "range_accel_mps2": range_accel_raw / 100.0,
    }


def parse_tspi_datagram(datagram: bytes) -> ParsedTSPI:
    """Parse a binary TSPI datagram into a :class:`ParsedTSPI` instance."""

    (
        message_type,
        version,
        sensor_id,
        day,
        time_ticks,
        status,
        status_flags,
    ) = _unpack_header(datagram)

    payload = datagram[_HEADER_SIZE:]
    time_s = time_ticks / 10_000.0
    status_mapping = _status_bits(status, status_flags)

    if message_type is MessageType.GEOCENTRIC:
        parsed_payload = _parse_geocentric(payload)
        return ParsedTSPI(
            type="geocentric",
            sensor_id=sensor_id,
            day=day,
            time_s=time_s,
            time_ticks=time_ticks,
            status=status,
            status_flags=status_mapping,
            payload=parsed_payload,
            version=version,
        )

    if message_type is MessageType.SPHERICAL:
        parsed_payload = _parse_spherical(payload)
        return ParsedTSPI(
            type="spherical",
            sensor_id=sensor_id,
            day=day,
            time_s=time_s,
            time_ticks=time_ticks,
            status=status,
            status_flags=status_mapping,
            payload=parsed_payload,
            version=version,
        )

    # Should not reach here because unsupported types are handled earlier.
    raise AssertionError("Unhandled message type")
