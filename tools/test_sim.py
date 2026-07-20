#!/usr/bin/env python3
"""frodas simulation integration test - the REAL firmware, no hardware.

Compiles esphome/sim-greenhouse.yaml (actual esp32 build), boots it under
Espressif QEMU against a throwaway authenticated mosquitto, drives it
through the web UI's HTTP API exactly like a person at the control panel
(harness: esphome_skills.sim_test), and asserts that the on-device rules
behave:

    1. node comes up: retained status "online" + MQTT discovery
    2. injected sensor values surface under the real sensor ids
    3. time 06:30 + dry soil  -> watering opens the irrigation valve
    4. time 23:00 (outside window) -> hard gate force-closes the valve
    5. hot & humid -> ventilation opens the roof vent
    6. battery 12.3 V -> load-shed tier 2;  11.8 V -> tier 3
    7. battery 13.4 V -> cascaded recovery back to Normal

    sudo -E nix develop .#sim -c python3 tools/test_sim.py

Slow: one esp32 compile (cached in .esphome-sim/) plus ~6 minutes of
emulated control-loop time. Not part of the default validation gate. See
the harness docstring for requirements (QEMU, udp/123, mosquitto).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import PROJECT  # noqa: E402

from esphome_skills.sim_test import run  # noqa: E402

# Retained injections published before the firmware boots: mild day, wet
# soil (valve stays closed), healthy battery.
BOOT = {"temperature": 21, "humidity": 60, "soil": 80,
        "battery": 13.2, "illuminance": 20000, "solar_current": 1.0}


def scenario(ctx) -> None:
    ctx.heading("2. injected sensors surface under real ids")
    soil = ctx.wait_state("sensor/soil_moisture",
                          lambda v: v and abs(float(v) - 80) < 1)
    ctx.check(soil is not None, "soil injection 80 % -> soil_moisture")
    batt = ctx.wait_state("sensor/battery_voltage",
                          lambda v: v and abs(float(v) - 13.2) < .1)
    ctx.check(batt is not None, "battery injection 13.2 V -> battery_voltage")

    ctx.heading("3. watering rule: 06:30 + dry soil opens the valve")
    ctx.set_time(6, 30)
    ctx.inject(soil=20)
    valve = ctx.wait_state("switch/irrigation_valve",
                           lambda v: v == "ON", timeout=180)
    ctx.check(valve is not None,
              "irrigation valve opened inside the watering window")

    ctx.heading("4. watering hard gate: 23:00 force-closes the valve")
    ctx.set_time(23, 0)
    valve = ctx.wait_state("switch/irrigation_valve",
                           lambda v: v == "OFF", timeout=180)
    ctx.check(valve is not None,
              "irrigation valve force-closed outside the window")

    ctx.heading("5. ventilation rule: hot & humid opens the roof vent")
    ctx.inject(temperature=33, humidity=88)
    vent = ctx.wait_state("cover/roof_vent",
                          lambda v: v in ("opening", "open"), timeout=180)
    ctx.check(vent is not None, "roof vent started opening")

    ctx.heading("6. load shedding escalates")
    ctx.inject(battery=12.3)
    tier = ctx.wait_state("sensor/load_shed_tier",
                          lambda v: v and float(v) == 2)
    ctx.check(tier is not None, "12.3 V -> tier 2")
    ctx.inject(battery=11.8)
    tier = ctx.wait_state("sensor/load_shed_tier",
                          lambda v: v and float(v) == 3)
    ctx.check(tier is not None, "11.8 V -> tier 3")
    state = ctx.wait_state("text_sensor/load_shed_state",
                           lambda v: "3" in v, timeout=60)
    ctx.check(state is not None, "load shed state text published")

    ctx.heading("7. recovery cascades with hysteresis headroom")
    ctx.inject(battery=13.4)
    tier = ctx.wait_state("sensor/load_shed_tier",
                          lambda v: v and float(v) == 0, timeout=120)
    ctx.check(tier is not None, "13.4 V -> back to Normal")


if __name__ == "__main__":
    sys.exit(run(PROJECT, scenario, boot_injections=BOOT))
