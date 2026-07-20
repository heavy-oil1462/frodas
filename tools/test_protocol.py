#!/usr/bin/env python3
"""frodas protocol integration test - no Docker, no hardware.

Boots a throwaway authenticated mosquitto, runs tools/mock_device.py
against it, and asserts the MQTT contract from docs/PROTOCOL.md (harness:
esphome_skills.protocol_test). Entities asserted here are frodas':
battery_voltage telemetry, the watering_soil_threshold setpoint and the
watering_enabled switch.

Run inside the devshell (needs mosquitto + mosquitto_passwd + paho-mqtt):
    nix develop -c python3 tools/test_protocol.py

Exit code 0 = all assertions passed. This is part of the validation gate
and runs in CI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import PROJECT  # noqa: E402

from esphome_skills.protocol_test import ProtocolSpec, run  # noqa: E402

SPEC = ProtocolSpec(
    telemetry_sensor="battery_voltage",
    telemetry_ok=lambda v: 10.0 < float(v) < 15.0,
    number="watering_soil_threshold",
    switch="watering_enabled",
)

if __name__ == "__main__":
    sys.exit(run(PROJECT, SPEC))
