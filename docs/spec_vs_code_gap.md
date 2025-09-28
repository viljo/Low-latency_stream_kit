# Spec vs. Implementation Gap Analysis

## Overview
The documentation and JetStream change specification now align with the packaging and
telemetry publishing features that had previously been missing. However, several claims
about TimescaleDB bootstrapping and generator ergonomics remain out of sync with the
current implementation. The sections below capture both the resolved items and the gaps
that still need attention.

## Resolved discrepancies
- ✅ The README's editable-install instructions are now backed by a fully populated
  `pyproject.toml`, allowing `pip install -e .[test]` to succeed as written.【F:README.md†L17-L36】【F:pyproject.toml†L1-L68】
-- ✅ The TSPI generator actually supports the documented UDP workflow: `--udp-target`
  arguments are parsed and forwarded alongside JetStream publishing when enabled.【F:README.md†L61-L66】【F:tspi_generator_flet.py†L17-L144】
- ✅ Player components subscribe to `tags.broadcast` and route tag events through
  `PlayerState._handle_tag`, matching the JetStream change specification's requirement
  for collaborative tag support.【F:docs/player_receiver_jetstream.md†L51-L70】【F:player_flet.py†L1-L139】【F:tspi_kit/ui/player.py†L151-L343】
- ✅ The player specification now reflects in-session command/tag history: it no
  longer promises a TimescaleDB bootstrap and documents the forward-seek replay
  behaviour implemented in `PlayerState`.【F:docs/player_receiver_jetstream.md†L51-L80】【F:tspi_kit/ui/player.py†L151-L367】
- ✅ The README's generator feature list now reflects the available styles and
  headless metrics output: `--style` toggles "normal" vs "airshow" formations
  and headless runs emit JSON metrics summarising frames, aircraft, and rate.【F:README.md†L61-L66】【F:tspi_generator_flet.py†L19-L148】【F:tspi_kit/generator.py†L10-L118】
- ✅ The karaoke-style channel and replay specification now has a concrete implementation in `tspi_kit.channels`, and the README documents the helper APIs for operators and clients.【F:docs/channels-replay-spec.md†L1-L133】【F:tspi_kit/channels.py†L1-L295】【F:README.md†L9-L110】
- ✅ The README's testing guidance now covers the Flet harness and UI tests that ship with the repository.【F:README.md†L97-L99】
- ✅ Channel heartbeat payloads now expose the optional operator, source IP, and ping fields promised in the channel specification and console documentation.【F:docs/channels-replay-spec.md†L65-L104】【F:docs/command_console.md†L96-L121】【F:tspi_kit/channels.py†L235-L274】

## Outstanding inconsistencies

None currently identified. Future updates should reassess once the offline map work or additional CLI surfaces ship.

## Summary
Packaging and JetStream publishing behaviour now match the published documentation, and
collaborative tag handling is wired up as specified. No further documentation gaps are
tracked at present, but the sheet should be revisited as new features land.
