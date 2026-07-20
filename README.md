# frodas 🌱

**Autonomous, solar-powered greenhouse controller** — ESP32 + ESPHome
firmware with fully local control loops, MQTT telemetry into VictoriaMetrics
+ Grafana, and first-class Home Assistant integration.

*frodas* is Swedish for "to thrive / flourish" — which is what both the
plants and the battery are supposed to do. (The project was originally
scaffolded as `greenhouse-controller`; frodas is shorter, memorable, and
greppable.)

## Design principles (non-negotiable)

1. **The greenhouse is autonomous.** Watering, ventilation, and load
   shedding run as ESPHome automations/lambdas *on the ESP32*. WiFi, MQTT,
   Home Assistant, and the entire server stack can be down for weeks and the
   plants still get watered and the battery still gets protected.
2. **Power-constrained by design.** Solar + 12 V LiFePO4. The CPU never
   sleeps (control loops must run) but the radio does: WiFi is off by
   default and wakes every 10 minutes (configurable) to publish retained
   telemetry and pick up retained setpoints. No deep sleep.
3. **Generic.** Different sensors/actuators are adopted through ESPHome
   packages + substitutions ([docs/EXTENDING.md](docs/EXTENDING.md)) —
   nobody should ever edit core logic.

## Architecture

```
        GREENHOUSE (autonomous)          │            HOUSE / SERVER
                                         │
  ┌────────────────────────────┐  WiFi   │   ┌─────────────┐
  │ ESP32 (ESPHome, CPU 24/7)  │ duty-   │   │  mosquitto   │ retained telemetry
  │                            │ cycled  │   │  (auth,      │ + retained setpoints
  │  sensors ─► control loops  │◄───────►│   │  persistent) │
  │  SHT3x     • watering      │  MQTT   │   └──┬───────┬───┘
  │  soil ADC  • ventilation   │         │      │       │ MQTT discovery
  │  BH1750    • load shedding │         │ ┌────▼───┐ ┌─▼──────────────┐
  │  INA219  ─► actuators      │         │ │telegraf│ │ Home Assistant │
  │  V-divider  valve/pump/vent│         │ └────┬───┘ │ setpoints,     │
  └────────────────────────────┘         │ ┌────▼──────────┐ alerts,   │
       solar ─► LiFePO4 12V              │ │VictoriaMetrics│ dashboard │
                                         │ └────┬──────────┘└──────────┘
                                         │ ┌────▼────┐
                                         │ │ Grafana │ auto-provisioned
                                         │ └─────────┘ dashboard
```

Load shedding (12 V LiFePO4 defaults, hysteresis on recovery):
**tier 1** < 12.8 V non-essential loads off · **tier 2** < 12.4 V watering
only + slower telemetry · **tier 3** < 12.0 V all actuators off, hourly beacon.

## Quickstart

Everything below runs inside the dev shell: `nix develop` (or install
esphome/yamllint/mosquitto/docker-compose/paho-mqtt yourself).

**1. Server** (any Docker host — a Pi is plenty; VictoriaMetrics idles at
~50 MB RAM):

```bash
python3 tools/stack.py init    # generates server/.env + MQTT credentials
python3 tools/stack.py up
python3 tools/stack.py smoke   # verifies MQTT -> telegraf -> VM -> Grafana
```

Grafana: http://localhost:3000 — the Frodas dashboard is auto-provisioned.

**2. Try it without hardware** — a full software twin of a node:

```bash
python3 tools/mock_device.py --discovery   # watch Grafana fill up; HA finds it
```

Or go one better and run the **real firmware** under QEMU in a container,
against your own broker and Home Assistant, with a web control panel to
inject sensor values and time of day and watch the actual on-device rules
react ([docs/SIMULATION.md](docs/SIMULATION.md)):

```bash
python3 tools/sim_container.py build
python3 tools/sim_container.py run --broker <mqtt-host> --username u --password p
# http://localhost:8080 — sliders, presets, live entity state
```

**3. Firmware**:

```bash
cp esphome/secrets.yaml.example esphome/secrets.yaml   # edit real values
esphome run esphome/example-greenhouse.yaml
```

Copy `example-greenhouse.yaml`, keep the packages you have hardware for,
override substitutions (pins, thresholds, calibration). Wiring, BOM and the
power budget worksheet: [docs/HARDWARE.md](docs/HARDWARE.md).

**4. Home Assistant**: entities appear automatically via MQTT discovery
(add the MQTT integration pointing at mosquitto). Then copy
`homeassistant/packages/greenhouse.yaml` (derived sensors + low-battery /
frost / offline alerts) and `homeassistant/blueprints/` into your config —
notes in the package header. Example Lovelace: `homeassistant/dashboards/`.

## Repository layout

```
esphome/                 firmware: greenhouse-base.yaml + composable packages/
server/                  docker compose: mosquitto, telegraf, VictoriaMetrics, grafana
homeassistant/           HA package, alert blueprint, lovelace example
docs/                    PROTOCOL.md · EXTENDING.md · HARDWARE.md · SIMULATION.md
tools/                   project.py · validate.py · stack.py · mock_device.py
                         sim_container.py · test_protocol.py · test_sim.py
.claude/skills/          agent skills wrapping the tools (verify, simulator, …)
```

The MQTT contract (topics, retention rules, availability semantics, the
sleeping-setpoint trick): [docs/PROTOCOL.md](docs/PROTOCOL.md). Read it
before integrating anything.

## Why VictoriaMetrics (and not InfluxDB 2)?

Both work; VM won on operational fit for a small always-on box: a single
static binary with ~10× lower memory than InfluxDB 2, one-flag retention,
and it ingests Telegraf's InfluxDB line protocol unchanged while exposing
PromQL to Grafana as a standard Prometheus datasource — no Flux, no tokens,
no buckets. Swapping back is a two-service change in
`server/docker-compose.yml` plus the Grafana datasource.

## Development

```bash
nix develop -c python3 tools/validate.py        # full validation gate (CI runs this)
nix develop -c python3 tools/test_protocol.py   # broker-level protocol test
sudo -E nix develop .#sim -c python3 tools/test_sim.py  # REAL firmware under QEMU (slow)
esphome compile esphome/example-greenhouse.yaml # full firmware build
```

NixOS notes: the flake pins everything the repo needs (`nix develop`).
On NixOS hosts the server stack runs fine with `virtualisation.docker.enable
= true` (or rewrite `server/docker-compose.yml` as `virtualisation.oci-containers`
if you prefer — the configs mount cleanly either way). The nixpkgs
`platformio` wrapper needs user namespaces; in restricted sandboxes fall
back to `pip install esphome` for `compile` (config validation is
unaffected).

## License

MIT — see [LICENSE](LICENSE).
