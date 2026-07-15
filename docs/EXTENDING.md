# Extending frodas

frodas is adopted by **composing packages and overriding substitutions** —
never by editing `greenhouse-base.yaml` or the stock packages. If you find
yourself editing core files to adapt to your hardware, that's a design bug;
open an issue.

## How composition works

Your node config (start from `esphome/example-greenhouse.yaml`) includes
packages and overrides their substitutions:

```yaml
substitutions:
  node_name: my-greenhouse
  soil_adc_pin: GPIO32          # overrides the package default
  watering_pulse_s: "60"

packages:
  base: !include greenhouse-base.yaml            # always
  radio: !include packages/radio-wifi.yaml       # exactly one radio package
  i2c: !include packages/bus-i2c.yaml            # once, if any I²C sensor
  soil: !include packages/sensor-soil-capacitive.yaml
  valve: !include packages/actuator-latching-valve.yaml
  watering: !include packages/automation-watering.yaml
  my_sensor: !include packages/sensor-my-thing.yaml   # your own
```

ESPHome merges substitutions with the **including file winning**, so package
defaults are exactly that — defaults. Values you must quote: anything used
inside a lambda as a number (`"120"`, `"12.8"`).

## The id contracts

Packages talk to each other only through entity/global ids, resolved via
substitutions so any package can be swapped for a compatible one:

| id (default) | Kind | Provided by | Consumed by |
|---|---|---|---|
| `load_shed_tier` | global int 0–3 | **base** (always 0 without the load-shedding package) | every actuator/automation package |
| `radio_on` / `radio_off` | scripts | the radio package (radio-wifi, radio-openeth) | base's radio-window scheduler |
| `net_time` | time component | base | automation-watering |
| `battery_voltage` | sensor (V) | sensor-battery | automation-load-shedding (`loadshed_battery_sensor`) |
| `soil_moisture` | sensor (%) | sensor-soil-capacitive, or watering-schedule-only (constant stub — schedule+budget watering without a probe) | automation-watering (`watering_soil_sensor`) |
| `greenhouse_temperature` / `greenhouse_humidity` | sensors | sensor-sht3x | automation-ventilation (`vent_temp_sensor` / `vent_rh_sensor`) |
| `irrigation_valve` | switch | actuator-latching-valve | automation-watering (`watering_valve`) |
| `vent` | cover | actuator-vent-motor | automation-ventilation (`vent_cover`) |
| `bus_a` | i2c bus | bus-i2c | all I²C sensor packages |

Swapping hardware = writing a package that provides the same id. A DS18B20
soil-temperature-compensated probe, an SHTC3 instead of the SHT3x, a
current-shunt battery monitor — all fine as long as the id and unit match.
The simulator is the extreme case: `packages/sim-sensors.yaml` provides all
the sensor ids from MQTT injections and `packages/radio-openeth.yaml`
provides the radio scripts for an emulated NIC, and every automation runs
unchanged (docs/SIMULATION.md).

## Where does *my* node config live?

Not in this repo — node configs are downstream consumers. Two good homes:

* **A private config repo cloned inside your frodas checkout** (frodas
  gitignores `frodas-config/`). Your config is substitutions + a package
  list with `!include ../esphome/...` paths, so it always builds against the
  frodas version you have checked out — ideal when you hack on both:

  ```bash
  cd frodas && git clone <your-private-config-repo> frodas-config
  esphome run frodas-config/greenhouse.yaml
  ```

* **ESPHome remote packages** — no local frodas checkout needed, pin a ref:

  ```yaml
  packages:
    frodas:
      url: https://github.com/heavy-oil1462/frodas
      ref: main            # pin a tag for production
      refresh: 1d
      files: [esphome/greenhouse-base.yaml, esphome/packages/radio-wifi.yaml, ...]
  ```

Every package file is standalone (no cross-`!include`s) and `!secret` always
resolves against *your* config's secrets.yaml, so both forms work unchanged.

## Radio (transport) packages

`greenhouse-base.yaml` never touches wifi directly. It drives the duty cycle
through two scripts that exactly one included radio package must provide:

* `radio_on` — bring the transport up
* `radio_off` — take it down (called only when "Radio Always On" is off)

`packages/radio-wifi.yaml` is the hardware implementation (wifi +
wifi.enable/disable + RSSI diagnostics); `packages/radio-openeth.yaml` the
QEMU one (MQTT-layer disconnect). An ethernet-wired or LoRa-bridged node is
a new radio package, not a base edit. Keep `reboot_timeout: 0s` semantics:
a frodas node must never reboot just because the network is absent.

## Writing a sensor package

`packages/sensor-dht22.yaml`:

```yaml
# =============================================================================
# frodas — packages/sensor-dht22.yaml
# DHT22 temp/RH on a single GPIO.
# Substitutions:
#   dht_pin  (default GPIO4)
#   dht_update_interval  (default 30s)
# Provides: sensor ids greenhouse_temperature (°C), greenhouse_humidity (%)
# =============================================================================
substitutions:
  dht_pin: GPIO4
  dht_update_interval: 30s

sensor:
  - platform: dht
    model: DHT22
    pin: ${dht_pin}
    update_interval: ${dht_update_interval}
    temperature:
      name: Greenhouse Temperature
      id: greenhouse_temperature
    humidity:
      name: Greenhouse Humidity
      id: greenhouse_humidity
```

Conventions (enforced in review):

1. **Header block** documenting every substitution, its default, and what
   ids the package *provides* / *requires*.
2. Substitutions for every pin, threshold, and interval. Sane defaults.
3. Short `update_interval` is fine — sampling is local and nearly free;
   publishing only happens during radio windows anyway (the base snapshots
   every entity on connect).
4. Keep entity **names** stable — object_ids derived from them feed
   Telegraf/Grafana/HA (see PROTOCOL.md).

## Writing an actuator package — safety checklist

Actuators must protect themselves **locally**; assume every automation and
the network can fail at any moment:

- [ ] Fail-safe boot state (`restore_mode: ALWAYS_OFF` or a re-driven close
      pulse like the latching valve).
- [ ] A hard runtime/travel cap on anything that draws power while active
      (see the pump watchdog script pattern in `actuator-pump-12v.yaml`).
- [ ] A local tier gate: an `interval:` that forces the actuator to its safe
      state when `id(load_shed_tier)` exceeds its tier (valve/pump: 3,
      vent movement: 2, your non-essential loads: 1).
- [ ] No retained MQTT commands (`command_retain` only on setpoints — replay
      hazard, PROTOCOL.md "Retention rules").

The tier ladder: tier 1 sheds *non-essential* loads (grow light, fan — your
call), tier 2 leaves watering only, tier 3 stops everything. Gate pattern:

```yaml
interval:
  - interval: 30s
    then:
      - if:
          condition:
            lambda: 'return id(load_shed_tier) >= 1 && id(grow_light).state;'
          then:
            - switch.turn_off: grow_light
```

## Writing an automation package

Follow `automation-watering.yaml` as the reference: one `interval:` control
loop, all runtime knobs as `number`/`switch`/`select` template entities with
`restore_value: true` + `command_retain: true`, hard gates first (each one
actively forces the safe state, not just skips), decision gates after.
Treat NaN sensor reads as a fault and choose the fail-safe branch
explicitly (no watering / hold vent / hold tier).

## Hooks

`automation-load-shedding.yaml` exposes a `loadshed_tier_changed` script that
runs on every tier change. Extend it from your node config without touching
the package:

```yaml
script:
  - id: !extend loadshed_tier_changed
    then:
      - if:
          condition:
            lambda: 'return id(load_shed_tier) >= 1;'
          then:
            - switch.turn_off: grow_light
```

## Server / dashboard side

New numeric sensors flow through automatically: Telegraf subscribes to
`frodas/+/sensor/+/state` and creates `<object_id>_value`. Add panels to
`server/grafana/dashboards/greenhouse.json` (export from the Grafana UI and
commit). New ON/OFF entities under `switch`/`binary_sensor`/`cover` are also
covered. Anything else needs a stanza in `server/telegraf/telegraf.conf`.

## Before you PR

```bash
nix develop -c python3 tools/validate.py        # must be green
nix develop -c python3 tools/test_protocol.py   # if you touched MQTT semantics
nix develop -c esphome compile esphome/example-greenhouse.yaml  # if you touched lambdas
```

If you changed protocol semantics, update `docs/PROTOCOL.md`,
`tools/mock_device.py`, and `tools/test_protocol.py` in the same PR.
