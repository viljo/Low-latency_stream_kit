# Low-latency_stream_kit

Open-source Python toolkit for working with FMV BAPS Realtime TSPI v4 telemetry. The suite ingests 37-byte TSPI datagrams, publishes them to NATS JetStream, replays captures, and simulates flights with both Qt5 GUIs and headless automation modes.

## Features
- **Producer** – UDP listener that parses TSPI datagrams, adds receive timestamps, and publishes CBOR payloads to JetStream with deduplication headers.
- **Receiver** – Durable JetStream consumer that decodes CBOR, validates against the Draft 2020-12 schema, and emits JSON lines or logs.
- **JetStream Player** – Qt5 application (GUI/headless) with rate control, seek scaffolding, metrics reporting, and room-based playout subjects backed by smoothed map previews.
- **PCAP Player** – Replays 37-byte TSPI frames from pcap/pcapng files into the UDP producer with adjustable rate, loop, and headless metrics.
- **TSPI Generator** – Synthetic flight track generator (normal or airshow) targeting UDP or JetStream outputs with configurable fleet size/rates and headless automation.
- **Schema & Tests** – Draft 2020-12 schema (`tspi.schema.json`) and pytest suite covering datagram parsing and schema validation.

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
4. **Play back in headless mode**
   ```bash
   python player_qt.py --headless --nats-server nats://127.0.0.1:4222 --duration 10
   ```

## Components
### producer.py
- UDP listener on port 30000 (configurable)
- Parses BAPS TSPI v4 datagrams → Python dict → CBOR
- Publishes to `tspi.geocentric.<sensor_id>` or `tspi.spherical.<sensor_id>`
- Dedup headers: `Nats-Msg-Id = "<sensor>:<day>:<time_s_int>"`
- Options: sensor filters, custom stream, multiple NATS servers

### receiver.py
- Durable consumer on `tspi.>`
- Decodes CBOR to JSON, optional schema validation
- Emits JSON lines by default (toggle with `--json-stream/--no-json-stream`)
- Batch pull with back-off on idle

### player_qt.py
- GUI: placeholder Qt5 window ready for controls/map integration
- Headless: consumes JetStream, reports metrics (`frames`, `rate`, `clock`, `room`)
- Flags: `--headless`, `--rate`, `--clock`, `--room`, `--metrics-interval`, `--exit-on-idle`

### pcap_player_qt.py
- Reads pcap/pcapng files (37-byte TSPI frames)
- Streams frames to UDP producer respecting capture timing × rate multiplier
- Headless metrics include frames sent, target host/port
- GUI placeholder for future full player UI

### tspi_generator_qt.py
- Simulates geocentric TSPI for configurable fleet sizes
- Styles: `normal` and `airshow`
- Outputs UDP datagrams and/or publishes directly to JetStream (CBOR)
- Headless metrics: frames generated, aircraft count, current rate

## JSON Schema
The Draft 2020-12 schema in `tspi.schema.json` enforces shared fields (`type`, `sensor_id`, `day`, `time_s`, `status`, `status_flags`) and payload-specific properties for geocentric and spherical telemetry. Validation is integrated into the receiver and exposed via `tspi_kit.schema.validate_payload`.

## Testing
```bash
pytest
```
Tests cover datagram parsing for both message types, schema validation, non-UI integration flows, and Qt5 GUI/headless behaviour. Run `pytest -k ui` to focus on interface validation driven by `pytest-qt`.

## Offline Maps
Map previews currently use placeholder widgets. Final integration will use PyQtWebEngine/OSM tiles with configurable smoothing (`--smooth-center`, `--smooth-zoom`, `--window-sec`) exposed via shared UI configuration (`tspi_kit.ui.app.UiConfig`). Headless modes remain fully operational without map assets.

## License
Apache License 2.0 — see `LICENSE`.
