---
name: simulator
description: Run/test the REAL frodas firmware without hardware — esp32 build under Espressif QEMU in a podman container against the user's own MQTT/HA, with a web UI to inject sensor values and time of day. Use for testing firmware rules end-to-end, the sim container (tools/sim_container.py), the rule integration test (tools/test_sim.py), or anything in sim/ / esphome/sim-greenhouse.yaml.
---

# frodas simulator (real firmware under QEMU)

Full docs: `docs/SIMULATION.md`. The simulator is the actual firmware
(esphome/sim-greenhouse.yaml, esp32 target) under Espressif QEMU. Sensor
values arrive via retained `frodas/<node>/sim/<key>` topics
(packages/sim-sensors.yaml); time of day via the fake SNTP server inside
sim/webui.py; the automation packages are the production ones.

## Commands

```bash
# container on the user's machine (podman or docker)
python3 tools/sim_container.py build
python3 tools/sim_container.py run --broker <host> --username u --password p
python3 tools/sim_container.py logs|status|stop     # logs = firmware serial

# integration test: throwaway broker + webui + QEMU + rule assertions
# (.#sim shell = default shell + qemu-esp32; may build QEMU once)
sudo -E nix develop .#sim -c python3 tools/test_sim.py  # slow (~compile + 6 min)

# web UI standalone against any broker (no container)
python3 sim/webui.py --broker <host> --http-port 8080 --ntp-port 1123
```

test_sim.py env overrides: `ESPHOME_BIN` (a pip esphome that can compile —
the nix platformio wrapper needs user namespaces), `QEMU_ESP32` (otherwise
qemu-system-xtensa from the devshell).

## Failure interpretation

- `cannot bind udp/123` — SNTP port is fixed in lwIP; run as root
  (`sudo -E`) or grant CAP_NET_BIND_SERVICE. Without it the firmware clock
  free-runs and time-of-day control silently does nothing.
- `tcp/1883 in use` (test) — the test broker must sit on 1883: the firmware
  is compiled for 10.0.2.2:1883 (QEMU user-net's view of the host).
- Firmware connects but no entity reacts to sliders — check the injected
  keys actually match (validate.py's `sim` check compares webui INJECTIONS
  with sim-sensors.yaml topics).
- Node never shows online with duty-cycled radio — that's the design; the
  webui publishes retained `radio_always_on ON` unless started with
  `--no-radio-always-on`.
- Broker unreachable from QEMU — user-mode networking NATs outbound only and
  can't resolve container-only names; `sim_container.py`/entrypoint resolve
  `MQTT_HOST` to an IP first. Never point the firmware at 127.0.0.1.
- Firmware crashloops at setup() (`assert failed: xQueueGenericSend`) —
  you booted a stale flash.bin. QEMU mutates the image in place (NVS);
  always regenerate via `sim._build.make_flash_image` before boot
  (entrypoint.py and test_sim.py already do).

## Keep in sync (contract)

sim-sensors.yaml topics ⇄ webui.py `INJECTIONS` ⇄ docs/SIMULATION.md table —
enforced by `tools/validate.py sim`. The sim reuses the real automation
packages, so entity object_ids here are the same load-bearing ids as
everywhere (CLAUDE.md). Transport contract (`radio_on`/`radio_off` scripts)
is defined in greenhouse-base.yaml's header; radio-openeth.yaml and
radio-wifi.yaml are the two implementations. After touching any of this:
`tools/validate.py` and, for rule-level changes, `tools/test_sim.py`.
