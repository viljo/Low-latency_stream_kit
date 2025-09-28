# Spec vs. Implementation Gap Analysis

## Overview
The current repository diverges from the published specifications in several key areas. Installation instructions reference packaging metadata that is not present, several JetStream integration features remain unimplemented, and the generator CLI cannot fulfil the documented UDP workflow. The sections below capture the gaps that still need to be addressed.

## Installation & Packaging
- ❌ The README directs contributors to install the toolkit via `pip install -e .[test]`, implying editable mode support backed by a `pyproject.toml` or equivalent packaging file.【F:README.md†L17-L36】 The repository root, however, does not ship any packaging metadata (no `pyproject.toml`, `setup.cfg`, or `setup.py`), so the editable install instructions cannot succeed as written.【fed0aa†L1-L4】

## TSPI Generator CLI
- ❌ The README advertises that the TSPI generator can target “UDP datagrams and/or publishes directly to JetStream.”【F:README.md†L54-L71】 In practice the CLI only exposes JetStream connectivity (or the in-memory simulator); there are no arguments for UDP targets or sockets in `tspi_generator_qt.py`, so the UDP output path described in the spec is missing.【F:tspi_generator_qt.py†L1-L92】

## Player / Receiver Integration
- ❌ The JetStream change specification requires the player to bootstrap from TimescaleDB (latest commands, recent tags) and to surface collaborative tag events with “seek to tag” support.【F:docs/player_receiver_jetstream.md†L37-L70】 The shipped player only instantiates JetStream consumers from the CLI, never touches the datastore, and routes messages exclusively through the in-memory/JetStream receivers.【F:player_qt.py†L14-L82】
- ❌ Tag subjects (`tags.*`) are not consumed anywhere in the player window or headless runner, so tag broadcasts described in the spec cannot appear in the UI. `PlayerState._handle_message` distinguishes only telemetry vs. command payloads and lacks any tag-handling branch.【F:tspi_kit/ui/player.py†L109-L172】

## Demo Environment Expectations
- ⚠️ The README describes a demo helper that launches a three-node JetStream cluster, dual TimescaleDB nodes, and orchestrates generator/player lifecycles.【F:README.md†L82-L98】 While the bundled `demo` script exists, it still depends on the unreconciled features above (Timescale bootstrapping, full tag flow). Until those gaps are closed, the documented “full demonstration environment” will not match runtime behaviour.

## Summary
Significant mismatches remain between the published docs/spec and the implementation: editable installs fail due to missing packaging metadata, the generator cannot emit UDP traffic, and the player omits the datastore/tag integrations mandated by the JetStream specification. Addressing these areas should be prioritised before declaring the toolkit feature-complete.
