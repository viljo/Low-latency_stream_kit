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
- ✅ The TSPI generator actually supports the documented UDP workflow: `--udp-target`
  arguments are parsed and forwarded alongside JetStream publishing when enabled.【F:README.md†L61-L66】【F:tspi_generator_qt.py†L17-L144】
- ✅ Player components subscribe to `tags.broadcast` and route tag events through
  `PlayerState._handle_tag`, matching the JetStream change specification's requirement
  for collaborative tag support.【F:docs/player_receiver_jetstream.md†L51-L70】【F:player_qt.py†L38-L70】【F:tspi_kit/ui/player.py†L151-L343】
- ✅ The player specification now reflects in-session command/tag history: it no
  longer promises a TimescaleDB bootstrap and documents the forward-seek replay
  behaviour implemented in `PlayerState`.【F:docs/player_receiver_jetstream.md†L51-L80】【F:tspi_kit/ui/player.py†L151-L367】

## Outstanding inconsistencies

### README generator feature list
The README still advertises generator "Styles: `normal` and `airshow`" and
"Headless metrics" output.【F:README.md†L61-L66】 The CLI exposes no style-related
arguments, and the headless branch simply runs the controller before exiting without
printing or streaming metrics.【F:tspi_generator_qt.py†L17-L154】 The existing generator
API only produces a single deterministic flight pattern, so the README oversells the
current feature set.

## Summary
Packaging and JetStream publishing behaviour now match the published documentation, and
collaborative tag handling is wired up as specified. Remaining misalignments are limited
to the README's lingering claims about multiple generator styles and headless metrics
output.
