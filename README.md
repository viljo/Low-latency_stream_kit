# Low-latency_stream_kit
Low-latency toolkit for BAPS TSPI v4 — ingest, replay, simulate &amp; visualize telemetry with JetStream and Qt5.

BAPS TSPI JetStream Player Suite

Open-source Python toolkit for working with the FMV BAPS Realtime TSPI v4 protocol.

Features:
• Producer – parses 37-byte TSPI UDP datagrams, adds receive timestamps, publishes to NATS JetStream in CBOR.
• Receiver – consumes JetStream, validates against JSON Schema, exports JSON/CBOR.
• Qt5 Players – interactive UI apps with OSM preview for live JetStream playback, PCAP file replay, and synthetic flight simulation.
• Headless mode – all apps support --headless for CI and batch use; emit JSON metrics for integration tests.
• Simulator – configurable TSPI generator (default 50 aircraft @ 50 Hz) with normal or airshow flight styles.
• Tests – integration tests (UI and non-UI) plus JetStream HA/chaos tests across 3-node clusters.

Built for low-latency telemetry, simulation, and replay with “YouTube-like” controls (play, pause, seek, rate).

Licensed under Apache-2.0.
