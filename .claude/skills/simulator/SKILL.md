---
name: simulator
description: Run/test the REAL frodas firmware without hardware - esp32 build under Espressif QEMU in a podman container against the user's own MQTT/HA, with a web UI to inject sensor values and time of day. Use for testing firmware rules end-to-end, the sim container (tools/sim_container.py), the rule integration test (tools/test_sim.py), or esphome/sim-greenhouse.yaml.
---

# frodas simulator (real firmware under QEMU)

Full docs: `docs/SIMULATION.md`. The simulator is the actual firmware
(esphome/sim-greenhouse.yaml, esp32 target) under Espressif QEMU. Sensor
values arrive via retained `frodas/<node>/sim/<key>` topics
(packages/sim-sensors.yaml); time of day via the fake SNTP server; the
automation packages are the production ones. Engine:
esphome_skills (simc/webui/sim_build/sim_test); repo side: injections and
presets in tools/project.py, rule assertions in tools/test_sim.py.

```bash
python3 tools/sim_container.py build
python3 tools/sim_container.py run --broker <host> --username u --password p
python3 tools/sim_container.py logs|status|stop     # logs = firmware serial

# integration test (.#sim shell = default + qemu-esp32; may build QEMU once)
sudo -E nix develop .#sim -c python3 tools/test_sim.py   # slow
```

env overrides: `ESPHOME_BIN`, `QEMU_ESP32`.

## Keep in sync (contract)

sim-sensors.yaml topics <-> tools/project.py injections <->
docs/SIMULATION.md table, enforced by `tools/validate.py sim`. Entity
object_ids are the same load-bearing ids as everywhere (CLAUDE.md).
Transport contract (`radio_on`/`radio_off` scripts) is defined in
greenhouse-base.yaml's header; radio-openeth.yaml and radio-wifi.yaml are
the two implementations. After touching any of this: `tools/validate.py`
and, for rule-level changes, `tools/test_sim.py`.

Canonical doc and failure interpretation (udp/123, tcp/1883, stale
flash.bin crashloop, QEMU networking):
https://github.com/heavy-oil1462/esphome-skills/blob/main/skills/simulator.md
