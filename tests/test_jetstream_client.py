from tspi_kit.jetstream_client import normalize_stream_subjects


def test_normalize_stream_subjects_removes_overlaps():
    subjects = [
        "tspi.>",
        "tspi.cmd.display.>",
        "tags.>",
        "tspi.cmd.display.units",
    ]
    assert normalize_stream_subjects(subjects) == ["tspi.>", "tags.>"]


def test_normalize_stream_subjects_keeps_distinct_prefixes():
    subjects = [
        "tags.>",
        "tags.broadcast",
        "tags.single.*",
        "tspi.cmd.display.>",
        "tspi.telemetry.*",
    ]
    assert normalize_stream_subjects(subjects) == [
        "tags.>",
        "tspi.cmd.display.>",
        "tspi.telemetry.*",
    ]


def test_normalize_stream_subjects_handles_wildcard_ordering():
    subjects = [
        "tspi.cmd.display.>",
        "tspi.>",
    ]
    assert normalize_stream_subjects(subjects) == ["tspi.>"]
