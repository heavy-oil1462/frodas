---
name: firmware
description: Build, flash, or debug the frodas ESPHome firmware (esphome config/compile/run/logs, secrets setup, adding packages to a node config). Use when the user wants firmware built or flashed, is editing esphome/*.yaml, or is adding support for new sensors/actuators.
---

# frodas firmware workflow

## Setup

```bash
cp esphome/secrets.yaml.example esphome/secrets.yaml   # then edit real values
```

## Build / flash

```bash
nix develop -c esphome config esphome/example-greenhouse.yaml   # fast validation
nix develop -c esphome compile esphome/example-greenhouse.yaml  # full C++ build
nix develop -c esphome run esphome/example-greenhouse.yaml      # flash via USB/OTA
nix develop -c esphome logs esphome/example-greenhouse.yaml
```

Sandbox note: nixpkgs' platformio wrapper needs user namespaces. If compile
fails with `bwrap: ... Operation not permitted`, use a pip venv instead:
`python3 -m venv .venv && .venv/bin/pip install esphome && .venv/bin/esphome compile ...`

`esphome config` on both `greenhouse-base.yaml` and `example-greenhouse.yaml`
is part of the validate skill; run a full `compile` after touching lambdas —
config validation does NOT catch C++ errors.

## Editing rules (the OSS contract, see docs/EXTENDING.md)

- NEVER put user-specific tweaks in `greenhouse-base.yaml` or
  `packages/*.yaml` — node configs override via substitutions.
- New hardware = new package file in `esphome/packages/`, documented header,
  substitutions with sane defaults. Follow the id contracts:
  `battery_voltage`, `soil_moisture`, `irrigation_valve`, `vent`,
  `greenhouse_temperature/humidity`, global `load_shed_tier` (0–3).
- Anything that must survive the radio being off: local automation +
  flash-restored setpoint. Anything HA-editable while the node sleeps:
  `command_retain: true` on the entity — setpoints only, never raw actuators.
- Logs while WiFi is duty-cycled: use USB (`esphome logs` only works during
  radio windows, or turn on the "Radio Always On" switch temporarily).
