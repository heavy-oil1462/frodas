# frodas hardware guide

Reference build for the stock packages. Everything is swappable — see
EXTENDING.md — but this exact combination is what `example-greenhouse.yaml`
compiles for.

## Bill of materials

| Qty | Part | Notes | ~Cost |
|----:|------|-------|------:|
| 1 | ESP32 DevKit (esp32dev, WROOM-32) | avoid boards with power-hungry USB chips always energized if you can | €6 |
| 1 | 12 V 20 Ah LiFePO4 battery w/ BMS | BMS handles cell-level protection; frodas load-sheds well above BMS cutoffs | €70 |
| 1 | 20–50 W solar panel | see power budget below | €40 |
| 1 | Solar charge controller (PWM is fine at this scale) | 12 V LiFePO4 profile | €15 |
| 1 | Buck converter 12 V → 5 V (≥1 A, low quiescent) | powers the ESP32; pick <1 mA idle draw | €5 |
| 1 | SHT31 breakout (I²C) | temp/RH | €5 |
| 1 | BH1750 breakout (I²C) | lux | €3 |
| 1 | Capacitive soil moisture probe v1.2 | throw away the resistive ones | €2 |
| 1 | INA219 breakout | solar charge current | €3 |
| 1 | 12 V latching (bistable) solenoid valve | zero hold power | €15 |
| 1 | DRV8871 or L298N H-bridge | drives the latching valve pulses | €4 |
| 1 | 12 V diaphragm pump (optional) | if not gravity-fed | €15 |
| 1 | Logic-level N-MOSFET module or relay (pump) | IRLZ44N-class | €2 |
| 1 | 12 V linear actuator or window motor + H-bridge (vent) | travel time ≈ 30 s | €25 |
| — | 100 kΩ + 20 kΩ resistors (battery divider), wire, enclosure, glands | IP65 enclosure recommended | €15 |

## Default pin map (all overridable via substitutions)

| Function | GPIO | Substitution | Constraint |
|---|---|---|---|
| I²C SDA / SCL | 21 / 22 | `i2c_sda` / `i2c_scl` | |
| Soil moisture ADC | 34 | `soil_adc_pin` | **ADC1 only** (32–39): ADC2 is unusable with WiFi. 34/35 are input-only. |
| Battery divider ADC | 35 | `battery_adc_pin` | ADC1 only |
| Valve open / close | 25 / 26 | `valve_open_pin` / `valve_close_pin` | |
| Pump MOSFET | 27 | `pump_pin` | |
| Vent open / close | 32 / 33 | `vent_open_pin` / `vent_close_pin` | |

Avoid strapping pins (0, 2, 12, 15) for outputs that must not glitch at boot.

## Wiring

```
   solar panel ──► charge controller ──► 12V LiFePO4 (BMS)
                        │                    │
                        │ (load out)         ├────────► H-bridge ──► latching valve
                   [INA219 high side         ├────────► MOSFET  ──► pump
                    on panel input]          ├────────► H-bridge ──► vent motor
                                             │
                                             ├─ 100k ─┬─ 20k ─ GND   (divider → GPIO35)
                                             │        └────────────► GPIO35
                                             └────► buck 12→5V ──► ESP32 5V/GND
```

The battery divider: 100 kΩ from battery + to the pin, 20 kΩ from the pin to
GND → ratio 6.0 (`battery_divider_ratio`), 15 V → 2.5 V at the ADC (12 dB
attenuation range). The 120 kΩ total keeps divider drain at ~0.1 mA. Add a
100 nF capacitor pin-to-GND to quiet ADC noise.

## Soil probe calibration

1. Flash, open logs (USB, or the "Radio Always On" switch + MQTT).
2. Probe in dry air → note "Soil Moisture Raw Voltage" → `soil_raw_dry_v`.
3. Probe in a glass of water up to the line → note voltage → `soil_raw_wet_v`.
4. Set both substitutions in your node config, re-flash. Values around
   2.8 V / 1.3 V are typical. Recalibrate installed in your actual soil for
   best absolute accuracy (dry vs. just-watered).

## Power budget worksheet

The design keeps the CPU always on (control loops) and duty-cycles only the
radio. Fill in your measured numbers; typical values:

| State | Current @ 5 V | Duty | Avg |
|---|---:|---:|---:|
| ESP32 CPU on, WiFi off | ~45 mA | ~93% | 42 mA |
| ESP32 WiFi window (connect+publish) | ~120 mA | ~7% (40 s / 10 min) | 8 mA |
| Sensors (SHT31+BH1750+INA219+probe) | ~2 mA | 100% | 2 mA |
| **ESP32 subsystem total** | | | **~52 mA @ 5 V ≈ 0.26 A·h/day × 5 V ≈ 1.3 Wh/day → ~2.6 Wh/day at 12 V side with buck losses** |

Actuators (per day, worst case): valve pulses are negligible (100 ms);
pump 5 min × 2 A = 0.17 Ah; vent 4 moves × 30 s × 1 A = 0.03 Ah.
**Total ≈ 0.5 Ah/day @ 12 V ≈ 6 Wh/day.**

Solar sizing rule of thumb: worst-month insolation × panel W × 0.7 ≥ 3× daily
need. A 20 W panel at 1 h effective winter sun ≈ 14 Wh/day — adequate with
the 20 Ah battery (≈ 30 days autonomy at 6 Wh/day before tier 3 stops
actuation; the beacon alone runs for months).

Numbers worth engineering against: doubling `telemetry_interval_min` from
10 → 20 saves ~4 mA average; the always-on CPU dominates. If your deployment
needs deep-sleep-class budgets, frodas is intentionally the wrong design —
it trades ~1 Wh/day for control loops that never stop.

## Enclosure notes

- Probe cable glands facing down; conformal-coat the soil probe's electronics
  end (the classic failure mode).
- The SHT31 wants shade and airflow: radiation shield or a vented corner,
  never above the soil you water.
- Keep H-bridge flyback paths short; twist motor pairs.
