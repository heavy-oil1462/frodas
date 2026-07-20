---
name: firmware
description: Build, flash, or debug the frodas ESPHome firmware (esphome config/compile/run/logs, secrets setup, adding packages to a node config). Use when the user wants firmware built or flashed, is editing esphome/*.yaml, or is adding support for new sensors/actuators.
---

# frodas firmware workflow

Compositions: `esphome/example-greenhouse.yaml` (hardware) and
`esphome/sim-greenhouse.yaml` (QEMU target); base is
`esphome/greenhouse-base.yaml`.

```bash
cp esphome/secrets.yaml.example esphome/secrets.yaml   # then edit real values
nix develop -c esphome config esphome/example-greenhouse.yaml
nix develop -c esphome compile esphome/example-greenhouse.yaml
nix develop -c esphome run esphome/example-greenhouse.yaml
```

## frodas id contract (docs/EXTENDING.md)

Never put user-specific tweaks in `greenhouse-base.yaml` or
`packages/*.yaml`; node configs override via substitutions. New hardware =
new package file with a documented header. Load-bearing object_ids:
`battery_voltage`, `soil_moisture`, `irrigation_valve`, `vent`,
`greenhouse_temperature/humidity`, global `load_shed_tier` (0-3). They span
telegraf metrics, the Grafana dashboard, the HA package and the mock;
rename only with a full sweep.

Anything that must survive the radio being off: local automation + a
flash-restored setpoint. HA-editable while the node sleeps:
`command_retain: true`, setpoints only, never raw actuators.

Canonical doc and landmines (bwrap/venv fallback, config-vs-compile,
git-tracked files under nix, duty-cycled logs):
https://github.com/heavy-oil1462/esphome-skills/blob/main/skills/firmware.md
