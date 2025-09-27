# Spec vs. Implementation Gap Analysis

## Overview
This document captures the current discrepancies between the published specifications/README and the implementation that ships in this repository.

## Producer / Ingestion Pipeline
- ✅ `producer.py` now exists as a standalone asyncio CLI. It binds to UDP, parses TSPI datagrams with `TSPIProducer`, and publishes to JetStream using the official NATS client, matching both the README and change specification.【F:producer.py†L1-L109】【F:README.md†L5-L54】

## Quick Start / CLI Parity
- ✅ `player_qt.py` and `tspi_generator_qt.py` now honour the documented `--nats-server` flag, establishing real JetStream connections with threaded adapters. The player exposes the documented `--json-stream/--no-json-stream` toggle, and both CLIs fall back to the in-memory simulator only when no server is supplied.【F:player_qt.py†L6-L83】【F:tspi_generator_qt.py†L6-L74】

## Player/Receiver (GUI/Headless)
- ✅ The player CLI builds durable JetStream consumers for live and historical subjects, allowing the GUI and headless runner to operate directly against external JetStream clusters. JSON streaming follows the README flag, and connections are closed cleanly when the application exits.【F:player_qt.py†L34-L98】

## Generator
- ✅ The generator CLI can publish to JetStream by spinning up the same threaded client, optionally creating the telemetry stream before publishing. The legacy in-memory mode remains available for tests, but the README workflow using `--nats-server` now functions end to end. UI sessions can automatically repeat via the `--continuous/--no-continuous` toggle consumed by the demo helper.【F:tspi_generator_qt.py†L38-L120】

## Persistence (Archiver, TimescaleDB, Replayer)
- ✅ **Resolved:** The archiver consumes real JetStream pull subscriptions and persists into TimescaleDB via an asyncpg-backed client that mirrors the documented schema. Historical playback pulls the canonical records and republishes to `player.<room>.playout` with paced timing, matching the spec.【F:tspi_kit/archiver.py†L1-L94】【F:tspi_kit/datastore.py†L1-L227】【F:tspi_kit/replayer.py†L1-L88】

## Demo helper
- ✅ The `./demo` helper now delivers the promised experience: it spins up a three-node JetStream cluster, instantiates an in-memory two-node HA datastore for the archiver, launches the generator and unified receiver/player UIs against real JetStream traffic, and coordinates graceful shutdown alongside datastore persistence.【F:README.md†L82-L95】【F:demo†L1-L760】

## Summary
The remaining toolkit components now align with the published README and JetStream integration specification. Producer, player, generator, and demo flows all operate against real JetStream clusters when requested while retaining in-memory shims for tests.【F:player_qt.py†L6-L106】【F:tspi_generator_qt.py†L6-L84】
