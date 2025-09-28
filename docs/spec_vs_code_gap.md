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
- ✅ The README's generator feature list now reflects the available styles and
  headless metrics output: `--style` toggles "normal" vs "airshow" formations
  and headless runs emit JSON metrics summarising frames, aircraft, and rate.【F:README.md†L61-L66】【F:tspi_generator_qt.py†L19-L148】【F:tspi_kit/generator.py†L10-L118】

## Outstanding inconsistencies

- ⚠️ The README's testing guidance still implies the upstream `pytest-qt` plugin drives the UI
  suite even though the repository provides a lightweight stub implementation and disables the
  real plugin via `pytest.ini`. The documentation should clarify that the stub is in place by
  default and outline how to run the tests against a full Qt runtime when desired.【F:README.md†L69-L78】【F:pytest.ini†L1-L2】【F:pytestqt/plugin.py†L1-L31】
- ⚠️ The README's offline maps section references CLI flags (`--smooth-center`, `--smooth-zoom`,
  `--window-sec`) and the module `tspi_kit.ui.app.UiConfig`, but the smoothing parameters are only
  available through the `UiConfig` dataclass in `tspi_kit.ui.config` and are not yet surfaced as
  command-line options.【F:README.md†L81-L88】【F:tspi_kit/ui/config.py†L1-L20】

## Summary
Packaging and JetStream publishing behaviour now match the published documentation, and
collaborative tag handling is wired up as specified. Outstanding doc updates should focus
on clarifying the bundled pytest-qt stub and the current entry points for map smoothing
configuration.
