# Simulating a greenhouse — the real firmware, no hardware

frodas ships a simulator that is not a re-implementation of the rules: it is
the **actual firmware**, compiled for a stock `esp32dev` board and executed
under [Espressif's QEMU fork](https://github.com/espressif/qemu), wired to
*your* MQTT broker and therefore *your* Home Assistant and Grafana. The
watering, ventilation, and load-shedding decisions you observe are made by
the same C++ that runs in the greenhouse.

```
┌────────────────────────── podman container ──────────────────────────┐
│                                                                      │
│  ┌───────────────────────────────┐      ┌─────────────────────────┐  │
│  │ QEMU (machine esp32)          │ SNTP │ webui.py                │  │
│  │  REAL firmware:               │─────►│  · simulated clock      │  │
│  │  sim-greenhouse.yaml          │ :123 │  · sensor injections    │  │
│  │  = greenhouse-base            │      │  · live entity view     │  │
│  │  + radio-openeth (emu eth)    │      └───────────▲─────────────┘  │
│  │  + sim-sensors (injectable)   │                  │ http :8080     │
│  │  + REAL actuators/automations │                  │                │
│  └───────────────┬───────────────┘                  │                │
└──────────────────┼──────────────────────────────────┼────────────────┘
                   │ MQTT (discovery, telemetry,      │
                   ▼        retained setpoints)     browser
            YOUR broker ──► YOUR Home Assistant / Grafana
```

The simulator engine (container build, web UI, QEMU glue) lives in the
shared [esphome-skills](https://github.com/heavy-oil1462/esphome-skills)
package; the frodas side is `tools/project.py` (injections, presets,
node names) and `tools/test_sim.py` (rule assertions).

## Quickstart

```bash
python3 tools/sim_container.py build                # once (~minutes)
python3 tools/sim_container.py run \
    --broker 192.168.1.10 --username frodas --password ...
python3 tools/sim_container.py logs                 # firmware serial console
```

Open **http://localhost:8080**: sliders for every sensor, a time-of-day
control, presets ("Dry morning", "Critical battery", …), and a live view of
everything the node publishes. The node appears in Home Assistant via MQTT
discovery as `frodas-sim`, indistinguishable from hardware.

The first `run` compiles the firmware inside the container against your
broker settings; the `frodas-sim-cache` volume makes later starts fast.
If the broker runs on the container host, use `--broker
host.containers.internal` (the entrypoint resolves it to an IP before baking
it into the firmware — QEMU's user-mode network can't see container DNS).

## How each input reaches the real firmware

| Input          | Mechanism |
|----------------|-----------|
| Sensor values  | Retained MQTT topics `frodas/<node>/sim/<key>`; `packages/sim-sensors.yaml` subscribes with the **real sensor ids** (`soil_moisture`, `battery_voltage`, …) so the stock automation packages run unmodified |
| Time of day    | webui.py answers the firmware's SNTP queries (`sntp_server: 10.0.2.2`, re-sync every 15 s) with a offset clock — set 06:30 and the watering-window logic genuinely believes it |
| Setpoints      | Nothing special — the standard retained `.../command` topics (PROTOCOL.md); edit them from HA or the UI like on hardware |

Injection keys: `temperature` °C · `humidity` % · `soil` % · `battery` V ·
`illuminance` lx · `solar_current` A. (`solar_power` is derived on-device.)
Until a value is injected the sensor has no state (NaN) and the fail-safes
behave exactly as with broken hardware: watering refuses to start, load
shedding holds its tier.

## Radio duty cycle in the simulator

An emulated NIC can't be powered down, so `packages/radio-openeth.yaml`
emulates the duty cycle at the MQTT layer: `radio_off` publishes the
retained `status: offline` (standing in for the LWT that an abrupt WiFi
power-down would trigger) and disconnects; `radio_on` reconnects. The
observable protocol is identical, except `offline` appears immediately
rather than one keepalive later.

By default the web UI publishes a retained `Radio Always On = ON` command at
startup so you get instant feedback while testing rules. Press
**Duty-cycled radio** (or flip the switch in HA) to watch real
radio-window behaviour — `telemetry_interval_min` is `2` in
`sim-greenhouse.yaml`, so windows come quickly.

## Testing "if this then that" from Home Assistant

Because the sim is a protocol-faithful node on your real broker, it is the
safe place to rehearse HA automations: point them at the `frodas-sim`
entities, drag the battery slider to 12.3 V, and check your low-battery
alert fires; set soil to 20 % at 06:30 and watch `switch.frodas_sim_
irrigation_valve` open — driven by the on-device rules, not HA.

## The automated version: tools/test_sim.py

The same loop, CI-style — throwaway mosquitto, web UI driven over HTTP,
assertions on the broker:

```bash
sudo -E nix develop .#sim -c python3 tools/test_sim.py
```

asserts boot/discovery, injection round-trips, watering window open + hard
close, ventilation opening, tier 2/3 escalation, and cascaded recovery.
Requirements: an esphome that can compile (pip fallback if the nix
platformio wrapper can't run its sandbox), `qemu-esp32` (in the `.#sim`
devshell via the `esphome-skills` flake input — kept out of the default
shell because it may build QEMU from source once), and the ability to bind udp/123
(root/CAP_NET_BIND_SERVICE) — lwIP's SNTP port is not configurable. Slow
(a compile plus ~6 min of emulated control-loop time); deliberately not part
of the default validation gate.

## Limitations

- **Not cycle-accurate**: QEMU timing ≈ wall clock, good enough for
  minute-scale control loops; don't benchmark on it.
- **GPIO goes nowhere**: valve pulses and motor pins drive emulated pins;
  observe actuators via their entities (which is what the server sees too).
- **Clean-disconnect offline**: see duty-cycle note above.
- **Timezone**: the UI's time slider is interpreted in `--timezone`
  (default Europe/Stockholm) — keep it equal to the firmware's `timezone`
  substitution, or 06:30 won't mean the same thing to both sides.
- **OTA/safe-mode exist but are pointless** in a container that rebuilds
  from source each config change.
