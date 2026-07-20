---
name: mock-device
description: Simulate a frodas greenhouse node over MQTT (realistic diurnal physics, load-shed tiers, watering pulses, HA discovery, duty-cycle mode). Use for demoing/testing the server stack, Grafana dashboards, or Home Assistant integration without hardware, and for generating test telemetry.
---

# frodas mock device

`tools/mock_device.py` is a software twin of a frodas node: same topics,
same retained/LWT semantics, same control-loop rules (docs/PROTOCOL.md).
Plumbing: esphome_skills.mock; the greenhouse physics (scenarios, watering,
tiers) is the repo side of the file. One simulated day passes in 5 minutes
by default.

```bash
# stack must be up; creds are read from server/.env automatically
python3 tools/mock_device.py
python3 tools/mock_device.py --scenario low-battery      # tiers escalate
python3 tools/mock_device.py --scenario frost            # frost alerts
python3 tools/mock_device.py --duty-cycle --interval 30  # radio windows
python3 tools/mock_device.py --discovery                 # + HA discovery
```

Scenarios: normal, sunny, cloudy, low-battery, frost.

Typical uses: populate Grafana (stack up + mock 10 min), test HA alerts
(`--discovery --scenario low-battery`), regression-test protocol changes
(the protocol-test skill runs this same mock under assertions).

Canonical doc and behaviours (clean exit vs SIGKILL/LWT, retained setpoint
commands):
https://github.com/heavy-oil1462/esphome-skills/blob/main/skills/mock-device.md
