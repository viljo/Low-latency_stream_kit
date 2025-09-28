# Channels & Replay Specification (JetStream, original‑rate replay)

**Repo:** https://github.com/viljo/Low-latency_stream_kit

**Objective:** Implement “karaoke channels” so an operator can start a **group replay** for all clients (clients may **override back to live**), and clients can run **private replays**. Clients can **discover and join** active channels. Clients **publish their current‑channel status** to the operator.

> 🎤 **Karaoke analogy:**  
> **livestream** = the main stage;  
> **replay <time>** = the song the DJ cues for everyone;  
> **client channels** = private booths. You can be invited to the stage, but you decide if you leave your booth mid‑song.

---

## 1) Naming, subjects, and streams

### Subjects (human‑facing “channels”)
- **Live channel (default):** `tspi.channel.livestream`
- **Group replay channels (admin):** `tspi.channel.replay.<identifier>` where `<identifier>` comes from the datastore tag or timestamp chosen for playback.
- **Private client channels (optional/advertised):** `tspi.channel.client.<clientId>.<sessionId>`

> Display names: `livestream`, `replay 2025-09-28T11:00:00Z`, `replay Intercept Window 3`, `client viljo/3f19`

### Streams (JetStream)
- **`TSPI`** — existing stream that ingests live telemetry (`tspi.geocentric.*`, `tspi.spherical.*`).
- **`TSPI_REPLAY`** — new, short‑retention stream that captures **channel subjects** (for discoverability / late joiners):
  - Subjects: `tspi.channel.replay.>`, `tspi.channel.client.>`
  - Suggested `max_age`: 1–6h (tune to needs)

> Rationale: Replay pacing is handled by a **push consumer** on `TSPI` (`ReplayPolicy=Original`), delivering into a channel subject. `TSPI_REPLAY` optionally persists those delivered messages so channels are discoverable and joinable even if clients momentarily disconnect.

---

## 2) Consumers (JetStream)

All consumers are **push** with **original‑rate replay** and low‑overhead acks.

- **LIVE_MAIN (durable)**
  - Stream: `TSPI`
  - `DeliverSubject`: `tspi.channel.livestream`
  - `DeliverPolicy`: `DeliverNew`
  - `AckPolicy`: `AckNone`
  - Enable `FlowControl` + `IdleHeartbeat`

- **REPLAY_<identifier> (admin‑triggered)**
  - Stream: `TSPI`
  - `DeliverSubject`: `tspi.channel.replay.<identifier>`
  - `DeliverPolicy`: `DeliverByStartTime` (or `ByStartSequence`) configured from the datastore reference captured in the identifier/tag
  - **`ReplayPolicy`: `ReplayOriginal`** (server‑paced)
  - `AckPolicy`: `AckNone`
  - `FlowControl` + `IdleHeartbeat`
  - Ephemeral by default; delete when done

- **CLIENT_<client>_<session> (optional private)**
  - Stream: `TSPI`
  - `DeliverSubject`: `tspi.channel.client.<client>.<session>`
  - `DeliverPolicy`: `ByStartTime` / `ByStartSequence`
  - **`ReplayPolicy`: `ReplayOriginal`**
  - `AckPolicy`: `AckNone`
  - `InactiveThreshold`: e.g., 2–5 min (auto‑cleanup)

---

## 3) Control, discovery, and status subjects

- **Admin control broadcast:** `tspi.ops.ctrl`  
  `GroupReplayStart` / `GroupReplayStop`

- **Channels directory (request‑reply):**
  - Request: `tspi.channel.list.req` (client sends with `reply=<inbox>`)
  - Response: list of active channels (see §6B)

- **Client status heartbeat → operator:** `tspi.ops.status`
  Announces `{client_id, state, channel_id, subject, override, ts, operator?, source_ip?, ping_ms?}`
  and is persisted in the `TSPI_OPS` stream so the command console can surface
  connection/replay/live-view events alongside the active-client roster.

---

## 4) Client behavior (states & transitions)

**States**
- `FOLLOWING_LIVESTREAM` — subscribed to `tspi.channel.livestream`
- `FOLLOWING_GROUP_REPLAY(channel=<identifier>)`
- `FOLLOWING_PRIVATE_REPLAY(channel=<clientId>.<sessionId>)`
- `LIVE_OVERRIDE` — same as `FOLLOWING_LIVESTREAM`, but while a group replay is active

**Transitions**
- **On `GroupReplayStart`**:
  - If on `FOLLOWING_LIVESTREAM` → auto‑switch to new `replay.<identifier>` channel
  - If on `FOLLOWING_PRIVATE_REPLAY` → **do not** switch; show prompt “Join group replay?”
- **Override (Back to Live)**: from any replay → switch to `livestream`
- **On `GroupReplayStop`**: clients in that group replay → back to `livestream`; private replay clients unchanged

**Channels list UI**
- Always shows: `livestream`
- Shows active: `replay.<identifier>` (admin) and `client.<id>.<session>` (public/private, as policy)
- Selecting a channel subscribes to its subject

**Status heartbeat**
- On every channel change and periodically (e.g., 5s), publish to `tspi.ops.status` announcing the current channel

**Tag creation**
  - Both receivers and the command console expose a **Tagg** action: pressing the
    button freezes the current UTC time, opens a comment field, and publishes the
    tag on `tags.broadcast` once saved. The annotation is persisted in
    TimescaleDB, appears immediately for live viewers, and is re-emitted at the
    original timestamp during replay.

---

## 5) Operator behavior

- **Start group replay**
  1) Create `REPLAY_<identifier>` push consumer on `TSPI` with `ReplayOriginal`, delivering to `tspi.channel.replay.<identifier>`
  2) Publish `GroupReplayStart` on `tspi.ops.ctrl`
  3) Optionally advertise channel to directory / ensure `TSPI_REPLAY` exists

- **Stop group replay**
  1) Delete (or let expire) `REPLAY_<identifier>` consumer
  2) Publish `GroupReplayStop` on `tspi.ops.ctrl`

- **Capture an operations tag**
  1) Press **Tagg** in the console to record the current UTC timestamp.
  2) Enter an operator comment and press **Save/Send**.
  3) The console publishes the tag on `tags.broadcast`, writes it to the
     TimescaleDB `tags` table, and surfaces the timestamp across all receivers
     immediately when live. During datastore playback the annotation is emitted
     again when the playhead reaches the captured timestamp so recorded events
     retain their original timing.

Playback continues until the datastore chunk naturally ends or operators send
`GroupReplayStop`.

> The administrator command console automates steps 1 and 2 by emitting the
> `GroupReplayStart`/`Stop` control payloads alongside a datastore identifier and
> stream target, ensuring every receiver joins the datastore replay in lockstep
> until the administrator stops the session.
>
> The GUI workflow requires operators to choose the datastore recording first,
> then select a startpoint via a timeline slider or a named tag before pressing
> **Play**. The resulting identifier captures both the recording metadata and
> startpoint so downstream systems can derive a unique channel subject for each
> replay.

---

## 6) Message schemas (JSON)

### A) Admin control (`tspi.ops.ctrl`)

```json
// GroupReplayStart
{
  "type": "GroupReplayStart",
  "channel_id": "replay.20250928T110000Z",
  "display_name": "replay 2025-09-28T11:00:00Z",
  "identifier": "2025-09-28T11:00:00Z",
  "stream": "TSPI"
}

// GroupReplayStop
{
  "type": "GroupReplayStop",
  "channel_id": "replay.20250928T110000Z"
}

