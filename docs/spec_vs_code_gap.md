# Spec vs. Implementation Gap Analysis

## Overview
This document captures the current discrepancies between the published specifications/README and the implementation that ships in this repository.

## Producer / Ingestion Pipeline
- ✅ `producer.py` now exists as a standalone asyncio CLI. It binds to UDP, parses TSPI datagrams with `TSPIProducer`, and publishes to JetStream using the official NATS client, matching both the README and change specification.【F:producer.py†L1-L109】【F:README.md†L5-L54】

## Quick Start / CLI Parity
- **README expectation:** The Quick Start shows end-to-end commands that pass `--nats-server` into the generator and player CLIs and toggles JSON streaming with `--json-stream`.【F:README.md†L29-L59】
- **Implementation reality:** Neither CLI recognises those switches. `player_qt.py` exposes only local playback arguments and lacks any networking flags; JSON streaming is controlled by a hard-to-disable `--stdout-json` flag instead of the documented toggle. The generator entry-point likewise accepts only local options and never opens UDP sockets or JetStream connections beyond the bundled in-memory simulator.【F:player_qt.py†L14-L55】【F:tspi_generator_qt.py†L15-L46】 The published Quick Start commands therefore fail as written.

## Player/Receiver (GUI/Headless)
- **README expectation:** `player_qt.py` serves as the unified player/receiver CLI, handling JSON line output, live ↔ historical switching, and direct JetStream connectivity via `--nats-server`. The change specification further requires JetStream subscriptions (`tspi.>`, `cmd.display.units`, `tags.broadcast`, `player.<room>.playout`) plus TimescaleDB lookups for commands/tags and unit conversions.【F:README.md†L7-L13】【F:README.md†L43-L59】【F:docs/player_receiver_jetstream.md†L50-L69】
- **Implementation reality:** `player_qt.py` instantiates an **in-memory** JetStream via `connect_in_memory` and never reaches out to NATS or TimescaleDB. All receivers are backed by the local simulator, so subjects like `cmd.display.units` or `tags.broadcast` are never subscribed. JSON output exists, but only behind the non-standard `--stdout-json` switch noted above.【F:player_qt.py†L29-L55】【F:tspi_kit/ui/player.py†L397-L471】

## Generator
- **README expectation:** `tspi_generator_qt.py` can emit UDP datagrams and/or publish directly to JetStream for downstream consumers.【F:README.md†L61-L65】
- **Implementation reality:** The generator wires itself to the same in-memory JetStream helper used by the player, creating no UDP socket and offering no way to target an external JetStream context. As a result it can only feed local consumers embedded in the process.【F:tspi_generator_qt.py†L24-L46】【F:tspi_kit/ui/player.py†L452-L466】

## Persistence (Archiver, TimescaleDB, Replayer)
- ✅ **Resolved:** The archiver consumes real JetStream pull subscriptions and persists into TimescaleDB via an asyncpg-backed client that mirrors the documented schema. Historical playback pulls the canonical records and republishes to `player.<room>.playout` with paced timing, matching the spec.【F:tspi_kit/archiver.py†L1-L94】【F:tspi_kit/datastore.py†L1-L227】【F:tspi_kit/replayer.py†L1-L88】

## Demo helper
- ✅ The `./demo` helper now delivers the promised experience: it spins up a three-node JetStream cluster, bridges the generator and receivers through real JetStream pull consumers, and drives the headless player runner against that infrastructure.【F:README.md†L82-L95】【F:demo†L1-L220】

## Summary
The README and JetStream integration spec still promise fully networked CLIs for the player and generator, but both entry points remain hard-wired to the in-memory JetStream simulator and omit the documented flags. Persistence and the demo environment now fulfil the specification, yet the GUI/headless tooling cannot be pointed at real NATS or TimescaleDB instances without significant additional development.
