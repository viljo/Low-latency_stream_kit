# Complete Change Specification — Player/Receiver Connects to JetStream

## 0. Scope

* Eliminate any direct coupling between the UDP Producer and Player/Receiver components.
* The Producer only ingests UDP datagrams and publishes to NATS JetStream.
* Every downstream consumer (Player/Receiver, Archiver, Replayer) integrates exclusively with JetStream.

## 1. Components & Roles

### Producer
* Ingest 37-byte BAPS TSPI v4 UDP datagrams (big-endian) and decode them per FMV guidance.
* Apply scaling rules:
  * Positions: `int32 / 100` → metres.
  * Velocities & accelerations: `int16 / 100` → m/s and m/s².
  * Angles: `uint32 / 1e6` → degrees.
  * Time: `uint32 / 10⁴` → seconds since midnight UTC.
* Augment each message with `recv_epoch_ms` and `recv_iso` timestamps.
* Encode the structured payload to CBOR and publish to JetStream on
  `tspi.geocentric.<sensor_id>` or `tspi.spherical.<sensor_id>`.
* Guarantee idempotent publishing by setting `Nats-Msg-Id = sensor_id:day:time_s`.
* Never expose a direct transport to Players/Receivers.

### NATS JetStream (central backbone)
* Serves as the only integration point for TSPI telemetry, commands, and tags.
* Key subjects:
  * `tspi.>` — live telemetry fan-out.
  * `tspi.cmd.display.>` — global display commands (units, marker color, ...).
  * `tags.create`, `tags.update`, `tags.delete`, `tags.broadcast` — collaborative annotations.
* Provides HA (`replicas=3`), consumer groups, and a persistence window for replay.

### Archiver
* Connects solely to JetStream.
* Subscribes to `tspi.>`, `tspi.cmd.display.>`, and `tags.*`.
* Persists into TimescaleDB while deduplicating via the JetStream message id.

### TimescaleDB Datastore
* Stores all JetStream messages (telemetry, commands, tags).
* Schema:
  * `messages` — append-only audit log with TSPI extracts.
  * `commands` — history of global commands.
  * `tags` — annotation records.
* Runs with HA (primary plus streaming replicas).

### Store Replayer
* Reads from TimescaleDB by time window or tag selection.
* Re-publishes historical CBOR payloads back to JetStream using `player.<room>.playout`.
* Paces playback using `recv_epoch_ms` or reconstructed TSPI timestamps.

### Player/Receiver
* Single GUI/headless application with live vs historical switching baked in.
* Establish JetStream subscriptions directly (no Producer link).
* Required subscriptions:
  * `tspi.>` — live telemetry and associated command stream.
  * `tags.broadcast` — collaborative tags.
  * `player.<room>.playout` — historical playback from the Store Replayer.
* Features:
  * Source selector for Live (JetStream) vs Historical (Datastore via Replayer).
  * Timeline scrubber ("YouTube" style) maintains a rolling history window for rewind/playhead jumps.
  * Headless mode offers JSON line streaming to integrate with log pipelines.
  * Commands apply immediately and cache global state (units, marker color, ...).
  * Tags display new annotations and support "seek to tag" for replay.
* On startup, query TimescaleDB for the latest command and recent tags.
* Command messages share the same JetStream stream as TSPI telemetry to guarantee that archival and replay reproduce the exact sequence of events.
  * Conversion for display only (DB remains SI units):
    * Altitude: metres ↔ feet.
    * Distance: metres/kilometres ↔ nautical miles.
    * Speed: metres per second ↔ knots ↔ km/h.
    * Acceleration: metres per second² ↔ feet per second².

## 2. Data Flow

```
UDP → Producer → JetStream → {Player/Receiver, Archiver, Replayer}
                                   ↓                 ↑
                                  TimescaleDB ← Store Replayer
```
