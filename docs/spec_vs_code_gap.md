# Spec vs. Implementation Gap Analysis

## Overview
This document captures the current discrepancies between the published specifications/README and the implementation that ships in this repository.

## Producer / Ingestion Pipeline
- **README expectation:** `producer.py` is a standalone UDP listener that connects to a live NATS JetStream cluster (`python producer.py --nats-server ...`).【F:README.md†L34-L45】【F:README.md†L47-L53】
- **Implementation reality:** The only producer is `tspi_kit.producer.TSPIProducer`, a helper that accepts already-received datagrams and publishes them via an injected publisher object; it never opens a UDP socket or a CLI entry point and is scoped to an in-memory JetStream helper.【F:tspi_kit/producer.py†L1-L48】 The repository root also has no `producer.py` script matching the advertised command.【83c4fc†L1-L4】
- **Spec expectation:** The change specification reiterates that the Producer is a UDP ingest process that publishes exclusively to JetStream.【F:docs/player_receiver_jetstream.md†L5-L22】

## Receiver / Consumer
- **README expectation:** `receiver.py` is a durable JetStream consumer CLI with toggles for JSON streaming (`--json-stream/--no-json-stream`).【F:README.md†L55-L59】
- **Implementation reality:** `tspi_kit.receiver.TSPIReceiver` is a thin wrapper around a provided consumer object with `fetch` helpers; there is no CLI, batching/back-off logic, or output streaming options.【F:tspi_kit/receiver.py†L1-L35】

## Player (GUI/Headless)
- **README expectation:** `player_qt.py` connects to a live JetStream cluster (via `--nats-server`), supports live ↔ historical switching, and exposes numerous CLI flags for remote connectivity.【F:README.md†L61-L65】【F:README.md†L42-L45】 The spec further requires direct JetStream subscriptions (`tspi.>`, `cmd.display.units`, `tags.broadcast`, `player.<room>.playout`) plus TimescaleDB lookups for commands/tags and unit conversions.【F:docs/player_receiver_jetstream.md†L50-L69】
- **Implementation reality:** `player_qt.py` only accepts local playback options, instantiates an **in-memory** JetStream (`connect_in_memory`) and never reaches out to NATS or TimescaleDB. There are no command/tag subscriptions or unit conversion features in the player state machine.【F:player_qt.py†L14-L55】【F:tspi_kit/ui/player.py†L1-L200】【F:tspi_kit/ui/player.py†L452-L466】

## Generator
- **README expectation:** `tspi_generator_qt.py` can emit UDP datagrams and/or publish directly to JetStream for downstream consumers.【F:README.md†L66-L70】
- **Implementation reality:** The generator reuses the same in-memory JetStream wiring as the player; it never exposes UDP sockets or remote JetStream connectivity.【F:tspi_generator_qt.py†L1-L46】

## Persistence (Archiver, TimescaleDB, Replayer)
- **Spec expectation:** JetStream is the authoritative backbone, and TimescaleDB stores telemetry/commands/tags with HA replication.【F:docs/player_receiver_jetstream.md†L24-L48】
- **Implementation reality:** All persistence helpers are built around an `InMemoryJetStream` simulation and a SQLite-backed `TimescaleDatastore` that only emulates the TimescaleDB schema. There is no HA orchestration, no actual JetStream client, and the replayer simply republishes from SQLite into the in-memory transport.【F:tspi_kit/jetstream_sim.py†L1-L144】【F:tspi_kit/datastore.py†L56-L146】【F:tspi_kit/archiver.py†L1-L58】【F:tspi_kit/replayer.py†L1-L59】

## Demo helper
- **README expectation:** `./demo` orchestrates a three-node JetStream cluster, generator, receiver, and headless player against real infrastructure.【F:README.md†L87-L99】
- **Implementation reality:** While the script scaffolds CLI plumbing, it operates on the same test-focused components that lack real JetStream/Timescale integrations, so the promised distributed environment cannot be realised with the current code.

## Summary
Across the toolkit, the public README and accompanying JetStream integration spec describe a fully networked system backed by NATS JetStream and TimescaleDB. The shipped implementation is an integration-test harness that keeps everything in-memory (or SQLite), with no UDP ingestion, JetStream connectivity, or database integrations. Aligning the code with the documentation will require substantial feature work in every component of the pipeline.
