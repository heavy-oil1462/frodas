# frodas MQTT protocol

This document is the contract between the firmware (`esphome/`), the server
stack (`server/`), Home Assistant (`homeassistant/`), and the mock device
(`tools/mock_device.py`). `tools/test_protocol.py` asserts it. If you change
semantics here, change all of them together.

## Topic tree

All topics live under `<root>/<node>` where `root` defaults to `frodas`
(substitution `mqtt_root`) and `node` is the ESPHome `node_name`.

```
frodas/<node>/status                                availability (LWT/birth), retained
frodas/<node>/sensor/<object_id>/state              numeric telemetry, retained
frodas/<node>/text_sensor/<object_id>/state         text telemetry, retained
frodas/<node>/binary_sensor/<object_id>/state       ON/OFF, retained
frodas/<node>/switch/<object_id>/state              ON/OFF, retained
frodas/<node>/switch/<object_id>/command            ON/OFF        <- HA/user writes
frodas/<node>/number/<object_id>/state              float, retained
frodas/<node>/number/<object_id>/command            float         <- HA/user writes
frodas/<node>/select/<object_id>/state              option string, retained
frodas/<node>/select/<object_id>/command            option string <- HA/user writes
frodas/<node>/cover/<object_id>/state               open/opening/closing/closed, retained
frodas/<node>/cover/<object_id>/command             OPEN/CLOSE/STOP
frodas/<node>/button/<object_id>/command            PRESS
```

`object_id` is the ESPHome entity name, lowercased with underscores
("Battery Voltage" → `battery_voltage`). These ids are **load-bearing**: the
Telegraf topic parser turns them into VictoriaMetrics metric names
(`<object_id>_value{node="<node>"}`), and the Grafana dashboard and HA
package reference them.

## The duty cycle, and what "offline" means

The node's radio is OFF by default. Every `telemetry_interval_min`
(default 10) it opens a **radio window**:

```
 wifi.enable ──► MQTT connect ──► publish "online" (retained)
             ──► publish full state snapshot (every entity, retained)
             ──► linger radio_linger_s (default 20 s):
                   retained setpoint commands arrive and are applied
             ──► wifi.disable
 …keepalive (15 s) expires ──► broker publishes LWT: status = "offline"
```

Consequences, all deliberate:

* `status` means **"radio window open right now"**, not "device alive".
  A healthy node is `offline` ~95% of the time.
* Load-shed tier 2 multiplies the period (default ×3); tier 3 drops to an
  hourly beacon (`loadshed_tier3_beacon_min`).
* **Device health = telemetry freshness.** A node that misses even the
  hourly beacon is genuinely down. The HA package alerts when the `uptime`
  sensor (published every window, always changing) is >90 min stale; the
  Grafana "Radio Availability" panel shows window cadence.

## Availability and Home Assistant discovery

ESPHome MQTT discovery is enabled (`homeassistant/...` prefix, retained), so
HA auto-creates every entity. The firmware **disables its birth message** and
publishes `status: online` manually. This is a deliberate trick: ESPHome only
attaches per-entity availability to discovery when birth and will messages
form a matched pair, so entities in HA **never go "unavailable"** while the
radio sleeps — they keep their retained state and stay editable. Do not
"fix" the missing birth message.

## Retention rules

| Payload                          | Retained? | Why |
|----------------------------------|-----------|-----|
| `status`                         | yes       | late subscribers must know the last state |
| all `*/state`                    | yes       | dashboards/HA must show data between radio windows |
| setpoint `*/command` (number, select, enable-type switches) | **yes** (`command_retain: true` in discovery) | edits made while the node sleeps are delivered at the next window |
| actuator `*/command` (valve/pump switches, cover, buttons)  | **no**  | replay hazard: a retained `ON` would re-fire at every reconnect — imagine the pump starting hours after you clicked |

The asymmetry in the last two rows is a safety property, not an oversight.
Manual actuator control therefore requires the node to be online (radio
window or the "Radio Always On" switch). Sleeping-safe control goes through
setpoints: e.g. force the vent with the retained "Ventilation Mode" select,
not the cover command.

Setpoints are also persisted to flash on the device (`restore_value`), so
they survive reboot and apply with no broker contact at all.

## Setpoints (retained, flash-persisted, HA-editable)

| Entity (object_id) | Type | Default | Meaning |
|---|---|---|---|
| `watering_enabled` | switch | ON | master enable for the watering loop |
| `watering_soil_threshold` | number, % | 35 | water when soil below this |
| `watering_window_start_hour` | number, h | 6 | schedule window start (local time) |
| `watering_window_end_hour` | number, h | 9 | schedule window end |
| `watering_max_daily_seconds` | number, s | 600 | hard cap on valve-open time per day |
| `vent_open_above_temperature` | number, °C | 28 | open vent above |
| `vent_close_below_temperature` | number, °C | 24 | close vent below (hysteresis gap) |
| `vent_open_above_humidity` | number, % | 85 | open vent above |
| `vent_close_below_humidity` | number, % | 70 | close vent below |
| `ventilation_mode` | select | Auto | Auto / Force open / Force closed |
| `radio_always_on` | switch | OFF | commissioning aid — defeats the power budget |

## Core telemetry (all retained)

| object_id | Unit | Source package |
|---|---|---|
| `greenhouse_temperature` | °C | sensor-sht3x |
| `greenhouse_humidity` | % | sensor-sht3x |
| `greenhouse_illuminance` | lx | sensor-light-bh1750 |
| `soil_moisture` (+ `soil_moisture_raw_voltage`) | %, V | sensor-soil-capacitive |
| `battery_voltage` | V | sensor-battery |
| `solar_current`, `solar_power`, `solar_bus_voltage` | A, W, V | sensor-battery (INA219) |
| `irrigation_valve` | ON/OFF | actuator-latching-valve |
| `water_pump` | ON/OFF | actuator-pump-12v |
| `roof_vent` | open/closed | actuator-vent-motor |
| `watering_used_today` | s | automation-watering |
| `load_shed_tier` | 0–3 | base (numeric mirror of the global) |
| `load_shed_state` | text | automation-load-shedding |
| `wifi_rssi` | dBm | base |
| `uptime` | s | base — freshness signal, see above |

## QoS and sessions

Everything is QoS 0 on the device side; correctness comes from retention,
not delivery guarantees — the next snapshot supersedes anything lost.
Telegraf subscribes with QoS 1 + persistent session so brief telegraf
restarts don't lose points.

## Worked example: changing a setpoint while the node sleeps

1. 12:00:05 — HA user drags "Watering Soil Threshold" to 40. HA publishes
   `frodas/gh1/number/watering_soil_threshold/command` = `40`, **retained**.
   The node is asleep; the broker stores the message.
2. 12:08:00 — node opens its radio window, connects, subscribes; broker
   delivers the retained `40`.
3. The number entity applies 40, saves it to flash, and echoes
   `.../state` = `40` (retained) — HA and Grafana converge.
4. Every watering decision from this tick on uses 40, even if the broker
   burns down afterwards.
