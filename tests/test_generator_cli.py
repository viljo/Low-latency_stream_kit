"""Tests for the TSPI generator CLI argument parsing."""
from __future__ import annotations

import pytest

from tspi_generator_qt import parse_args


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.jetstream is True
    assert args.udp_targets == []


def test_parse_args_udp_targets() -> None:
    args = parse_args(["--no-jetstream", "--udp-target", "127.0.0.1:45000"])
    assert args.jetstream is False
    assert args.udp_targets == [("127.0.0.1", 45000)]


def test_parse_args_requires_output() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--no-jetstream"])


def test_parse_args_invalid_udp_target() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--udp-target", "bad-target"])
