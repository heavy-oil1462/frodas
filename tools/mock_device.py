#!/usr/bin/env python3
"""frodas mock device — a software twin of a frodas ESPHome node.

Speaks the exact MQTT contract from docs/PROTOCOL.md so you can develop and
test the server stack, Grafana dashboards, and Home Assistant integration
without hardware:

  * LWT `<root>/<node>/status` -> "offline" (retained); "online" on connect
  * retained telemetry on `<root>/<node>/sensor/<object_id>/state`
  * retained actuator states (switch/cover) and setpoint states (number/
    select/switch)
  * subscribes to `*/command` topics and echoes accepted values to the
    retained state topic, like ESPHome does
  * optional Home Assistant MQTT discovery (--discovery)
  * optional radio duty cycle simulation (--duty-cycle): connect, publish a
    snapshot, linger, disconnect ungracefully-on-kill just like the firmware

The simulated greenhouse has real dynamics: diurnal temperature/illuminance,
soil that dries out and gets watered by the same control-loop rules the
firmware uses (window, threshold, daily cap, soak), and a battery that charges
from the sun and triggers load-shed tiers. One simulated day passes in
--day-seconds (default 300 s) so you can watch a full cycle in minutes.

Usage:
    python3 tools/mock_device.py                          # creds from server/.env
    python3 tools/mock_device.py --scenario low-battery --discovery
    python3 tools/mock_device.py --duty-cycle --interval 30
    python3 tools/mock_device.py --broker 192.168.1.10 --username x --password y

Scenarios: normal, sunny, cloudy, low-battery, frost.
Stop with Ctrl-C (clean "offline") or SIGKILL to test the broker's LWT.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import SERVER_DIR, load_env, mqtt_client  # noqa: E402

SCENARIOS = {
    #            temp base, temp amp, sun scale, battery start V
    "normal":      (18.0,      8.0,     1.0,      13.1),
    "sunny":       (24.0,     12.0,     1.3,      13.3),
    "cloudy":      (15.0,      4.0,     0.3,      12.9),
    "low-battery": (18.0,      8.0,     0.15,     12.45),
    "frost":       (2.0,       4.0,     0.4,      12.9),
}

TIER_THRESHOLDS = (12.8, 12.4, 12.0)  # tier 1 / 2 / 3
TIER_HYSTERESIS = 0.25
TIER_NAMES = ["Normal", "Tier 1 - conserve", "Tier 2 - critical", "Tier 3 - emergency"]


class MockGreenhouse:
    """Simulated plant/power physics + the firmware's local control loops."""

    def __init__(self, scenario: str, day_seconds: float):
        self.temp_base, self.temp_amp, self.sun_scale, batt = SCENARIOS[scenario]
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

        # setpoints — mirror the ESPHome number/select/switch entities
        self.setpoints: dict[str, float | str | bool] = {
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

    # --- simulated time -------------------------------------------------
    def sim_hour(self) -> float:
        return ((time.time() - self.start) / self.day_seconds * 24.0 + 8.0) % 24.0

    def sun(self) -> float:
        """0..1 daylight curve."""
        h = self.sim_hour()
        return max(0.0, math.sin((h - 6.0) / 12.0 * math.pi)) if 6.0 <= h <= 20.0 else 0.0

    # --- physics tick ----------------------------------------------------
    def tick(self, dt: float) -> None:
        sun = self.sun() * self.sun_scale
        vent_cooling = 4.0 * self.vent_pos

        self.temp = self.temp_base + self.temp_amp * sun - vent_cooling
        self.rh = max(20.0, min(99.0, 85.0 - self.temp * 1.2 + (20.0 if self.valve_open else 0.0)))
        self.lux = 80000.0 * sun

        # soil dries with heat, watering wets it
        self.soil -= (0.4 + 0.05 * max(0.0, self.temp - 15.0)) * dt / 60.0
        if self.valve_open:
            self.soil += 8.0 * dt / 60.0
            self.watering_used_s += dt
        self.soil = max(0.0, min(100.0, self.soil))

        # battery: solar in, base load + pump out
        self.solar_current = 2.0 * sun
        charge = (self.solar_current - 0.06 - (0.5 if self.valve_open else 0.0)) * dt / 3600.0
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
        in_window = sp["watering_window_start_hour"] <= h < sp["watering_window_end_hour"]
        over_budget = self.watering_used_s >= sp["watering_max_daily_seconds"]

        if self.valve_open:
            pulse_done = (time.time() - (self.valve_opened_at or 0)) >= 10.0
            if (self.tier >= 3 or not self.watering_enabled or not in_window
                    or over_budget or pulse_done):
                self.valve_open = False
                self.last_pulse_end = time.time()
            return
        if (self.watering_enabled and self.tier < 3 and in_window and not over_budget
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
        if self.temp >= sp["vent_open_above_temperature"] or self.rh >= sp["vent_open_above_humidity"]:
            self.vent_pos = 1.0
        elif (self.temp <= sp["vent_close_below_temperature"]
              and self.rh <= sp["vent_close_below_humidity"]):
            self.vent_pos = 0.0


class MockDevice:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.root = args.root
        self.node = args.node
        self.prefix = f"{args.root}/{args.node}"
        self.sim = MockGreenhouse(args.scenario, args.day_seconds)
        self.client = mqtt_client(f"mock-{args.node}", args.username, args.password)
        self.client.will_set(f"{self.prefix}/status", "offline", retain=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # --- MQTT contract ----------------------------------------------------
    def on_connect(self, *_a, **_kw) -> None:
        self.client.publish(f"{self.prefix}/status", "online", retain=True)
        for component in ("number", "select", "switch"):
            self.client.subscribe(f"{self.prefix}/{component}/+/command")
        self.publish_snapshot()
        print(f"[mock] connected as {self.prefix}")

    def on_message(self, _client, _userdata, msg) -> None:
        parts = msg.topic.split("/")
        component, object_id = parts[-3], parts[-2]
        payload = msg.payload.decode()
        sim = self.sim
        print(f"[mock] command: {component}/{object_id} = {payload}")

        if component == "number" and object_id in sim.setpoints:
            try:
                sim.setpoints[object_id] = float(payload)
            except ValueError:
                return
            self.pub("number", object_id, f"{float(payload):g}")
        elif component == "switch" and object_id == "watering_enabled":
            sim.watering_enabled = payload == "ON"
            self.pub("switch", object_id, payload)
        elif component == "switch" and object_id == "radio_always_on":
            sim.radio_always_on = payload == "ON"
            self.pub("switch", object_id, payload)
        elif component == "select" and object_id == "ventilation_mode":
            if payload in ("Auto", "Force open", "Force closed"):
                sim.vent_mode = payload
                self.pub("select", object_id, payload)

    def pub(self, component: str, object_id: str, value) -> None:
        self.client.publish(f"{self.prefix}/{component}/{object_id}/state",
                            str(value), retain=True)

    def publish_snapshot(self) -> None:
        sim = self.sim
        sensors = {
            "greenhouse_temperature": f"{sim.temp:.1f}",
            "greenhouse_humidity": f"{sim.rh:.0f}",
            "greenhouse_illuminance": f"{sim.lux:.0f}",
            "soil_moisture": f"{sim.soil:.0f}",
            "battery_voltage": f"{sim.battery_v:.2f}",
            "solar_current": f"{sim.solar_current:.2f}",
            "solar_power": f"{sim.solar_power:.1f}",
            "wifi_rssi": f"{-55 - 10 * sim.sun():.0f}",
            "uptime": f"{time.time() - sim.uptime0:.0f}",
            "load_shed_tier": str(sim.tier),
            "watering_used_today": f"{sim.watering_used_s:.0f}",
        }
        for object_id, value in sensors.items():
            self.pub("sensor", object_id, value)
        self.pub("switch", "irrigation_valve", "ON" if sim.valve_open else "OFF")
        self.pub("switch", "water_pump", "OFF")
        self.pub("switch", "watering_enabled", "ON" if sim.watering_enabled else "OFF")
        self.pub("switch", "radio_always_on", "ON" if sim.radio_always_on else "OFF")
        self.pub("cover", "roof_vent", "open" if sim.vent_pos > 0 else "closed")
        self.pub("select", "ventilation_mode", sim.vent_mode)
        self.pub("text_sensor", "load_shed_state", TIER_NAMES[sim.tier])
        for object_id, value in sim.setpoints.items():
            self.pub("number", object_id, f"{value:g}")

    def publish_discovery(self) -> None:
        """Minimal HA MQTT discovery, mirroring what ESPHome would publish."""
        device = {
            "identifiers": [f"mock-{self.node}"],
            "name": self.node,
            "manufacturer": "frodas (mock)",
        }
        entities = [
            ("sensor", "greenhouse_temperature", {"unit_of_measurement": "°C",
             "device_class": "temperature"}),
            ("sensor", "greenhouse_humidity", {"unit_of_measurement": "%",
             "device_class": "humidity"}),
            ("sensor", "soil_moisture", {"unit_of_measurement": "%"}),
            ("sensor", "battery_voltage", {"unit_of_measurement": "V",
             "device_class": "voltage"}),
            ("sensor", "solar_power", {"unit_of_measurement": "W",
             "device_class": "power"}),
            ("sensor", "load_shed_tier", {}),
            ("switch", "irrigation_valve", {}),
            ("switch", "watering_enabled", {}),
            ("number", "watering_soil_threshold",
             {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%"}),
        ]
        for component, object_id, extra in entities:
            config = {
                "name": object_id.replace("_", " ").title(),
                "unique_id": f"mock_{self.node}_{object_id}",
                "state_topic": f"{self.prefix}/{component}/{object_id}/state",
                "device": device,
                **extra,
            }
            if component in ("switch", "number"):
                config["command_topic"] = f"{self.prefix}/{component}/{object_id}/command"
                config["retain"] = True  # retained commands: PROTOCOL.md
            self.client.publish(
                f"homeassistant/{component}/{self.node}/{object_id}/config",
                json.dumps(config), retain=True)
        print("[mock] published HA discovery")

    # --- main loop ----------------------------------------------------------
    def run(self) -> int:
        args = self.args
        try:
            self.client.connect(args.broker, args.port, keepalive=args.keepalive)
        except OSError as err:
            print(f"[mock] cannot connect to {args.broker}:{args.port}: {err}",
                  file=sys.stderr)
            return 1
        self.client.loop_start()
        time.sleep(0.5)
        if args.discovery:
            self.publish_discovery()

        stop_at = time.time() + args.duration if args.duration else None

        def clean_exit(*_sig) -> None:
            print("\n[mock] clean shutdown (retained status stays 'online'; "
                  "SIGKILL me to test LWT)")
            self.client.loop_stop()
            self.client.disconnect()
            sys.exit(0)

        signal.signal(signal.SIGINT, clean_exit)
        signal.signal(signal.SIGTERM, clean_exit)

        last = time.time()
        while stop_at is None or time.time() < stop_at:
            now = time.time()
            self.sim.tick(now - last)
            last = now

            if args.duty_cycle:
                self.publish_snapshot()
                time.sleep(2)  # linger: let retained commands arrive
                self.client.publish(f"{self.prefix}/status", "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
                print(f"[mock] radio off, sleeping {args.interval}s "
                      f"(tier {self.sim.tier}, batt {self.sim.battery_v:.2f} V)")
                time.sleep(args.interval)
                self.client.reconnect()
                self.client.loop_start()
                time.sleep(0.5)
            else:
                self.publish_snapshot()
                print(f"[mock] t={self.sim.sim_hour():04.1f}h "
                      f"temp={self.sim.temp:.1f}°C soil={self.sim.soil:.0f}% "
                      f"batt={self.sim.battery_v:.2f}V tier={self.sim.tier} "
                      f"valve={'ON' if self.sim.valve_open else 'off'} "
                      f"vent={self.sim.vent_pos:.0%}")
                time.sleep(args.interval)

        clean_exit()
        return 0


def main() -> int:
    env = load_env(SERVER_DIR / ".env")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default=env.get("MQTT_USER"))
    parser.add_argument("--password", default=env.get("MQTT_PASSWORD"))
    parser.add_argument("--root", default="frodas", help="MQTT topic root")
    parser.add_argument("--node", default="frodas-greenhouse")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal")
    parser.add_argument("--interval", type=float, default=10.0,
                        help="seconds between telemetry publishes (default 10)")
    parser.add_argument("--day-seconds", type=float, default=300.0,
                        help="wall seconds per simulated day (default 300)")
    parser.add_argument("--duration", type=float, default=0,
                        help="stop after N seconds (default: run forever)")
    parser.add_argument("--keepalive", type=int, default=10)
    parser.add_argument("--duty-cycle", action="store_true",
                        help="simulate the radio duty cycle (connect/publish/disconnect)")
    parser.add_argument("--discovery", action="store_true",
                        help="publish Home Assistant MQTT discovery configs")
    args = parser.parse_args()
    return MockDevice(args).run()


if __name__ == "__main__":
    sys.exit(main())
