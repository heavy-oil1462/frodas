#!/usr/bin/env python3
"""frodas mock device - a software twin of a frodas ESPHome node.

The MQTT plumbing (LWT, retained state, command echo, HA discovery, duty
cycle) lives in esphome_skills.mock; this file is the greenhouse physics:
diurnal temperature/illuminance, soil that dries out and gets watered by
the same control-loop rules the firmware uses (window, threshold, daily
cap, soak), and a battery that charges from the sun and triggers load-shed
tiers. One simulated day passes in --day-seconds (default 300 s).

Usage:
    python3 tools/mock_device.py                          # creds from server/.env
    python3 tools/mock_device.py --scenario low-battery --discovery
    python3 tools/mock_device.py --duty-cycle --interval 30
    python3 tools/mock_device.py --broker 192.168.1.10 --username x --password y

Scenarios: normal, sunny, cloudy, low-battery, frost.
Stop with Ctrl-C (clean "offline") or SIGKILL to test the broker's LWT.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import PROJECT  # noqa: E402

from esphome_skills import mock  # noqa: E402

TIER_THRESHOLDS = (12.8, 12.4, 12.0)  # tier 1 / 2 / 3
TIER_HYSTERESIS = 0.25
TIER_NAMES = ["Normal", "Tier 1 - conserve", "Tier 2 - critical",
              "Tier 3 - emergency"]


class MockGreenhouse:
    """Simulated plant/power physics + the firmware's local control loops."""

    SCENARIOS = {
        #            temp base, temp amp, sun scale, battery start V
        "normal":      (18.0,      8.0,     1.0,      13.1),
        "sunny":       (24.0,     12.0,     1.3,      13.3),
        "cloudy":      (15.0,      4.0,     0.3,      12.9),
        "low-battery": (18.0,      8.0,     0.15,     12.45),
        "frost":       (2.0,       4.0,     0.4,      12.9),
    }

    def __init__(self, scenario: str, day_seconds: float):
        self.temp_base, self.temp_amp, self.sun_scale, batt = \
            self.SCENARIOS[scenario]
        self.day_seconds = day_seconds
        self.battery_v = batt
        self.soil = 55.0
        self.tier = 0
        self.valve_open = False
        self.vent_pos = 0.0
        self.watering_used_s = 0.0
        self.valve_opened_at: float | None = None
        self.last_pulse_end = 0.0
        self.start = time.time()
        self.uptime0 = time.time()

        # setpoints - mirror the ESPHome number/select/switch entities
        self.setpoints: dict[str, float] = {
            "watering_soil_threshold": 35.0,
            "watering_window_start_hour": 6.0,
            "watering_window_end_hour": 9.0,
            "watering_max_daily_seconds": 600.0,
            "vent_open_above_temperature": 28.0,
            "vent_close_below_temperature": 24.0,
            "vent_open_above_humidity": 85.0,
            "vent_close_below_humidity": 70.0,
        }
        self.watering_enabled = True
        self.radio_always_on = False
        self.vent_mode = "Auto"
        # initialize derived readings so a snapshot can be published
        # before the first real tick (on_connect fires immediately)
        self.tick(0.0)

    # --- simulated time ---------------------------------------------------
    def sim_hour(self) -> float:
        return ((time.time() - self.start) / self.day_seconds * 24.0
                + 8.0) % 24.0

    def sun(self) -> float:
        """0..1 daylight curve."""
        h = self.sim_hour()
        return max(0.0, math.sin((h - 6.0) / 12.0 * math.pi)) \
            if 6.0 <= h <= 20.0 else 0.0

    # --- physics tick -----------------------------------------------------
    def tick(self, dt: float) -> None:
        sun = self.sun() * self.sun_scale
        vent_cooling = 4.0 * self.vent_pos

        self.temp = self.temp_base + self.temp_amp * sun - vent_cooling
        self.rh = max(20.0, min(99.0, 85.0 - self.temp * 1.2
                                + (20.0 if self.valve_open else 0.0)))
        self.lux = 80000.0 * sun

        # soil dries with heat, watering wets it
        self.soil -= (0.4 + 0.05 * max(0.0, self.temp - 15.0)) * dt / 60.0
        if self.valve_open:
            self.soil += 8.0 * dt / 60.0
            self.watering_used_s += dt
        self.soil = max(0.0, min(100.0, self.soil))

        # battery: solar in, base load + pump out
        self.solar_current = 2.0 * sun
        charge = (self.solar_current - 0.06
                  - (0.5 if self.valve_open else 0.0)) * dt / 3600.0
        self.battery_v = max(10.5, min(14.4, self.battery_v + charge * 0.8))
        self.solar_power = self.solar_current * max(self.battery_v, 0.1)

        self.update_tier()
        self.control_watering()
        self.control_ventilation()

    def update_tier(self) -> None:
        v, t = self.battery_v, self.tier
        t1, t2, t3 = TIER_THRESHOLDS
        if v < t3:
            t = 3
        elif v < t2:
            t = max(t, 2)
        elif v < t1:
            t = max(t, 1)
        if t == 3 and v >= t3 + TIER_HYSTERESIS:
            t = 2
        if t == 2 and v >= t2 + TIER_HYSTERESIS:
            t = 1
        if t == 1 and v >= t1 + TIER_HYSTERESIS:
            t = 0
        self.tier = t

    def control_watering(self) -> None:
        sp = self.setpoints
        h = self.sim_hour()
        in_window = (sp["watering_window_start_hour"] <= h
                     < sp["watering_window_end_hour"])
        over_budget = self.watering_used_s >= sp["watering_max_daily_seconds"]

        if self.valve_open:
            pulse_done = (time.time() - (self.valve_opened_at or 0)) >= 10.0
            if (self.tier >= 3 or not self.watering_enabled or not in_window
                    or over_budget or pulse_done):
                self.valve_open = False
                self.last_pulse_end = time.time()
            return
        if (self.watering_enabled and self.tier < 3 and in_window
                and not over_budget
                and self.soil < sp["watering_soil_threshold"]
                and time.time() - self.last_pulse_end > 30.0):
            self.valve_open = True
            self.valve_opened_at = time.time()

    def control_ventilation(self) -> None:
        if self.tier >= 2:
            return
        sp = self.setpoints
        if self.vent_mode == "Force open":
            self.vent_pos = 1.0
            return
        if self.vent_mode == "Force closed":
            self.vent_pos = 0.0
            return
        if (self.temp >= sp["vent_open_above_temperature"]
                or self.rh >= sp["vent_open_above_humidity"]):
            self.vent_pos = 1.0
        elif (self.temp <= sp["vent_close_below_temperature"]
              and self.rh <= sp["vent_close_below_humidity"]):
            self.vent_pos = 0.0

    # --- esphome_skills.mock interface --------------------------------------
    def entities(self) -> list[tuple[str, str, str]]:
        sensors = {
            "greenhouse_temperature": f"{self.temp:.1f}",
            "greenhouse_humidity": f"{self.rh:.0f}",
            "greenhouse_illuminance": f"{self.lux:.0f}",
            "soil_moisture": f"{self.soil:.0f}",
            "battery_voltage": f"{self.battery_v:.2f}",
            "solar_current": f"{self.solar_current:.2f}",
            "solar_power": f"{self.solar_power:.1f}",
            "wifi_rssi": f"{-55 - 10 * self.sun():.0f}",
            "uptime": f"{time.time() - self.uptime0:.0f}",
            "load_shed_tier": str(self.tier),
            "watering_used_today": f"{self.watering_used_s:.0f}",
        }
        out = [("sensor", object_id, value)
               for object_id, value in sensors.items()]
        out += [
            ("switch", "irrigation_valve",
             "ON" if self.valve_open else "OFF"),
            ("switch", "water_pump", "OFF"),
            ("switch", "watering_enabled",
             "ON" if self.watering_enabled else "OFF"),
            ("switch", "radio_always_on",
             "ON" if self.radio_always_on else "OFF"),
            ("cover", "roof_vent", "open" if self.vent_pos > 0 else "closed"),
            ("select", "ventilation_mode", self.vent_mode),
            ("text_sensor", "load_shed_state", TIER_NAMES[self.tier]),
        ]
        out += [("number", object_id, f"{value:g}")
                for object_id, value in self.setpoints.items()]
        return out

    def handle_command(self, component: str, object_id: str,
                       payload: str) -> str | None:
        if component == "number" and object_id in self.setpoints:
            try:
                self.setpoints[object_id] = float(payload)
            except ValueError:
                return None
            return f"{float(payload):g}"
        if component == "switch" and object_id == "watering_enabled":
            self.watering_enabled = payload == "ON"
            return payload
        if component == "switch" and object_id == "radio_always_on":
            self.radio_always_on = payload == "ON"
            return payload
        if component == "select" and object_id == "ventilation_mode":
            if payload in ("Auto", "Force open", "Force closed"):
                self.vent_mode = payload
                return payload
        return None

    def discovery_entities(self) -> list[tuple[str, str, dict]]:
        return [
            ("sensor", "greenhouse_temperature",
             {"unit_of_measurement": "°C", "device_class": "temperature"}),
            ("sensor", "greenhouse_humidity",
             {"unit_of_measurement": "%", "device_class": "humidity"}),
            ("sensor", "soil_moisture", {"unit_of_measurement": "%"}),
            ("sensor", "battery_voltage",
             {"unit_of_measurement": "V", "device_class": "voltage"}),
            ("sensor", "solar_power",
             {"unit_of_measurement": "W", "device_class": "power"}),
            ("sensor", "load_shed_tier", {}),
            ("switch", "irrigation_valve", {}),
            # retained commands for setpoints only, never raw actuators
            ("switch", "watering_enabled", {"retain": True}),
            ("number", "watering_soil_threshold",
             {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%",
              "retain": True}),
        ]

    def status_line(self) -> str:
        return (f"t={self.sim_hour():04.1f}h temp={self.temp:.1f}C "
                f"soil={self.soil:.0f}% batt={self.battery_v:.2f}V "
                f"tier={self.tier} "
                f"valve={'ON' if self.valve_open else 'off'} "
                f"vent={self.vent_pos:.0%}")


if __name__ == "__main__":
    sys.exit(mock.main(PROJECT, MockGreenhouse))
