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
- **Group replay channels (admin):** `tspi.channel.replay.<YYYYMMDDTHHMMSSZ>`
- **Private client channels (optional/advertised):** `tspi.channel.client.<clientId>.<sessionId>`

> Display names: `livestream`, `replay 2025-09-28T11:00:00Z`, `client viljo/3f19`

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

- **REPLAY_<ts> (admin‑triggered)**
  - Stream: `TSPI`
  - `DeliverSubject`: `tspi.channel.replay.<YYYYMMDDTHHMMSSZ>`
  - `DeliverPolicy`: `DeliverByStartTime` (or `ByStartSequence`)
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
  Announces `{client_id, state, channel_id, subject, override, ts}`

---

## 4) Client behavior (states & transitions)

**States**
- `FOLLOWING_LIVESTREAM` — subscribed to `tspi.channel.livestream`
- `FOLLOWING_GROUP_REPLAY(channel=<ts>)`
- `FOLLOWING_PRIVATE_REPLAY(channel=<clientId>.<sessionId>)`
- `LIVE_OVERRIDE` — same as `FOLLOWING_LIVESTREAM`, but while a group replay is active

**Transitions**
- **On `GroupReplayStart`**:
  - If on `FOLLOWING_LIVESTREAM` → auto‑switch to new `replay.<ts>` channel
  - If on `FOLLOWING_PRIVATE_REPLAY` → **do not** switch; show prompt “Join group replay?”
- **Override (Back to Live)**: from any replay → switch to `livestream`
- **On `GroupReplayStop`**: clients in that group replay → back to `livestream`; private replay clients unchanged

**Channels list UI**
- Always shows: `livestream`
- Shows active: `replay.<ts>` (admin) and `client.<id>.<session>` (public/private, as policy)
- Selecting a channel subscribes to its subject

**Status heartbeat**
- On every channel change and periodically (e.g., 5s), publish to `tspi.ops.status` announcing the current channel

---

## 5) Operator behavior

- **Start group replay**
  1) Create `REPLAY_<ts>` push consumer on `TSPI` with `ReplayOriginal`, delivering to `tspi.channel.replay.<ts>`
  2) Publish `GroupReplayStart` on `tspi.ops.ctrl`
  3) Optionally advertise channel to directory / ensure `TSPI_REPLAY` exists

- **Stop group replay**
  1) Delete (or let expire) `REPLAY_<ts>` consumer
  2) Publish `GroupReplayStop` on `tspi.ops.ctrl`

---

## 6) Message schemas (JSON)

### A) Admin control (`tspi.ops.ctrl`)

```json
// GroupReplayStart
{
  "type": "GroupReplayStart",
  "channel_id": "replay.20250928T110000Z",
  "display_name": "replay 2025-09-28T11:00:00Z",
  "start": "2025-09-28T11:00:00Z",
  "end": "2025-09-28T11:05:00Z",
  "stream": "TSPI"
}

// GroupReplayStop
{
  "type": "GroupReplayStop",
  "channel_id": "replay.20250928T110000Z"
}

