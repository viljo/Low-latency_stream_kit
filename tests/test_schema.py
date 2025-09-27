"""Schema behaviour for TSPI payloads."""

import pytest
from jsonschema import ValidationError

from tspi_kit.schema import load_schema, validate_payload


def _status_flags(all_valid: bool = True) -> dict:
    value = bool(all_valid)
    return {
        "position_x_valid": value,
        "position_y_valid": value,
        "position_z_valid": value,
        "velocity_x_valid": value,
        "velocity_y_valid": value,
        "velocity_z_valid": value,
        "acceleration_x_valid": value,
        "acceleration_y_valid": value,
        "acceleration_z_valid": value,
    }


def _base_payload() -> dict:
    return {
        "type": "geocentric",
        "sensor_id": 10,
        "day": 12,
        "time_s": 1.25,
        "status": 5,
        "status_flags": _status_flags(),
        "recv_epoch_ms": 1_694_761_234,
        "recv_iso": "2024-01-01T12:34:56.789000+00:00",
        "payload": {
            "x_m": 0.0,
            "y_m": 0.0,
            "z_m": 0.0,
            "vx_mps": 0.0,
            "vy_mps": 0.0,
            "vz_mps": 0.0,
            "ax_mps2": 0.0,
            "ay_mps2": 0.0,
            "az_mps2": 0.0,
        },
    }


def test_schema_metadata() -> None:
    schema = load_schema()
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["type"]["enum"] == ["geocentric", "spherical"]


def test_validate_geocentric_payload() -> None:
    payload = _base_payload()
    validate_payload(payload)


def test_validate_spherical_payload() -> None:
    payload = _base_payload()
    payload["type"] = "spherical"
    payload["payload"] = {
        "range_m": 123.0,
        "azimuth_deg": 0.1,
        "elevation_deg": -0.05,
        "azimuth_rate_dps": 0.0,
        "elevation_rate_dps": 0.0,
        "range_rate_mps": 0.0,
        "azimuth_accel_dps2": 0.0,
        "elevation_accel_dps2": 0.0,
        "range_accel_mps2": 0.0,
    }
    validate_payload(payload)


def test_missing_field_raises_validation_error() -> None:
    payload = _base_payload()
    del payload["payload"]

    with pytest.raises(ValidationError):
        validate_payload(payload)
