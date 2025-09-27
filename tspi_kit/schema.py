"""Schema loading and validation helpers for TSPI payloads."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).with_name("tspi.schema.json")


@lru_cache(maxsize=1)
def load_schema() -> Mapping[str, Any]:
    """Load and cache the TSPI JSON schema as a mapping."""

    return json.loads(_SCHEMA_PATH.read_text("utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema())


def validate_payload(payload: Mapping[str, Any]) -> None:
    """Validate ``payload`` against the TSPI schema."""

    validator = _validator()
    validator.validate(payload)
