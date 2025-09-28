# Low-latency_stream_kit

Open-source Python toolkit for working with FMV BAPS Realtime TSPI v4 telemetry. The suite ingests 37-byte TSPI datagrams, publishes them to NATS JetStream, replays captures, and simulates flights with both Qt5 GUIs and headless automation modes.

## Features
- **Producer** – UDP listener that parses TSPI datagrams, adds receive timestamps, and publishes CBOR payloads to JetStream with deduplication headers.
- **Player/Receiver** – Single Qt5/CLI application that consumes JetStream telemetry, validates CBOR against the Draft 2020-12 schema, offers JSON/headless streaming, and provides live ↔ historical playback with timeline scrubbing, rate control, metrics, and smoothed map previews.
- **PCAP Replayer** – Utility to ingest 37-byte TSPI captures and push them into the JetStream pipeline for offline testing.
- **TSPI Generator** – Synthetic flight track generator (normal or airshow) targeting UDP or JetStream outputs with configurable fleet size/rates and headless automation.
- **Command Console** – Operator workspace that issues broadcast commands and metadata (display units, marker colour, session metadata), launches or stops datastore-backed group replays, and visualises an active-client roster with client status, displays a live operations log (with incoming and outgoing status and command messages).
- **Schema & Tests** – Draft 2020-12 schema (`tspi.schema.json`) and pytest suite covering datagram parsing and schema validation.
- **Channel Orchestration** – `tspi_kit.channels` implements the karaoke-style channel and replay specification, generating JetStream consumer configs, control payloads, and discovery listings for live, group replay, and private client channels.

## Getting Started
### Prerequisites
- Python 3.11+
- NATS JetStream cluster (e.g. `nats-server --jetstream`)
- Optional GUI tooling: install with the `ui` extra for PyQt5 and qasync support

### Installation
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[test]
# For GUI components
pip install -e .[ui]
```

### Quick Start

1. **Start NATS JetStream**
   ```bash
   nats-server --jetstream --store_dir=/tmp/nats-js
   ```
2. **Run the producer**
   ```bash
   python producer.py --nats-server nats://127.0.0.1:4222
   ```
3. **Generate synthetic traffic (headless)**
   ```bash
   python tspi_generator_qt.py --headless --nats-server nats://127.0.0.1:4222 --duration 10
   ```
4. **Consume and play back telemetry**
   ```bash
   python player_qt.py --headless --source live --nats-server nats://127.0.0.1:4222 --duration 10 --json-stream
   ```

## Components
### producer.py
- Standalone asyncio CLI that binds to UDP (default port 30000)
- Parses BAPS TSPI v4 datagrams → Python dict → CBOR
- Publishes to `tspi.geocentric.<sensor_id>` or `tspi.spherical.<sensor_id>`
- Dedup headers: `Nats-Msg-Id = "<sensor>:<day>:<time_s>"`
- Options: sensor filters (`--sensor-id`), custom stream prefix, multiple NATS servers,
  publish timeout, log level

### player_qt.py
- Unified GUI/headless JetStream receiver with live vs historical source selector
- Optional JSON line output (`--json-stream/--no-json-stream`) for headless log pipelines
- Timeline scrubber supports "YouTube-style" scrolling through buffered frames
- Flags: `--headless`, `--source`, `--rate`, `--clock`, `--room`, `--metrics-interval`, `--exit-on-idle`

### tspi_generator_qt.py
- Simulates geocentric TSPI for configurable fleet sizes
- Styles: `normal` and `airshow` (toggle with `--style` for aerobatic loops)
- Outputs UDP datagrams and/or publishes directly to JetStream (CBOR)
- Headless metrics: frames generated, aircraft count, current rate (JSON on stdout)
- UI mode can continuously regenerate batches via `--continuous/--no-continuous`

### command_console_qt.py
- Administrator-facing console for issuing live commands to receivers.
- Adds an operations view with events recieved from clients and commands sent to clients.
- Displays active clients in a sortable table including streaming channel,
  streaming status, connection time, last seen, user login name, and ping roundtrip delay.
- Broadcasts session metadata updates (friendly name plus identifier) alongside
  units and marker colour commands for downstream clients so every receiver
  shows the current livestream context.
- Supports GUI and headless automation (`--headless`) for operational scripts.
- Auto-provisions telemetry and operations streams when connected to JetStream
  and falls back to an in-memory broker for demos/tests.
- can enable select and issue a replay from datastore for all clients
- See [`docs/command_console.md`](docs/command_console.md) for usage examples.

### Channel helpers

`tspi_kit.channels` provides the primitives described in [`docs/channels-replay-spec.md`](docs/channels-replay-spec.md):

- `ChannelDirectory` tracks discoverable channels (`livestream`, operator-triggered group replays, and optional client-private replays).
- `ChannelManager` emits the `GroupReplayStart`/`GroupReplayStop` control payloads and ensures the directory stays in sync when operators start or stop a replay.
- `ChannelStatus` models the client heartbeat published to `tspi.ops.status`, while `live_consumer_config`/`replay_consumer_config` return the JetStream consumer definitions specified in the document.
- `replay_advertisement_subjects()` exposes the subjects to retain in the auxiliary `TSPI_REPLAY` stream so late joiners can discover active channels.

The helper module keeps the documented naming (`tspi.channel.*`) and timestamp conventions centralised so both the operator tooling and clients remain consistent.

## JSON Schema
The Draft 2020-12 schema in `tspi.schema.json` enforces shared fields (`type`, `sensor_id`, `day`, `time_s`, `status`, `status_flags`) and payload-specific properties for geocentric and spherical telemetry. Validation is integrated into the receiver and exposed via `tspi_kit.schema.validate_payload`.

## Testing
```bash
pytest
```
Tests cover datagram parsing for both message types, schema validation, non-UI integration flows, and Qt5 GUI/headless behaviour. A lightweight stub of the `pytest-qt` plugin ships with the repo so UI tests can execute without the real Qt event loop; the real plugin is disabled in `pytest.ini` via `-p no:pytestqt`. Install the `ui` extra (and `pytest-qt`) if you want to exercise the applications against an actual Qt runtime, then re-enable the real fixture set with:

```bash
PYTEST_ADDOPTS="" pytest -p pytestqt
```

Run `pytest -k ui` to focus on the interface-oriented checks once the desired plugin is active.

## Offline Maps
Map previews currently use placeholder widgets. Planned PyQtWebEngine/OSM integration will reuse the existing `UiConfig` dataclass (`tspi_kit.ui.config.UiConfig`) which already exposes smoothing parameters such as `smooth_center`, `smooth_zoom`, and `window_sec`; these values are presently configured programmatically rather than through CLI flags. Headless modes remain fully operational without map assets.

## License
Apache License 2.0 — see `LICENSE`.

### Demo helper script
The `./demo` helper orchestrates a full demonstration environment. It verifies system
and Python dependencies, launches a three-node NATS JetStream cluster, provisions an
in-memory two-node Timescale datastore HA pair for the archiver, starts the TSPI data
generator UI in continuous mode, and opens the unified receiver/player UI against
that infrastructure. Run it from the repository root:

```bash
./demo
```

Use `--duration` to stop automatically after a fixed interval or `Ctrl+C` to exit
manually. Additional options such as `--count` and `--rate` adjust the simulator load,
and `--log-dir` stores JetStream configuration and logs in a persistent directory.

