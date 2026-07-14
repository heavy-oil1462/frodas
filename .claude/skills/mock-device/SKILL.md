---
name: mock-device
description: Simulate a frodas greenhouse node over MQTT (realistic diurnal physics, load-shed tiers, watering pulses, HA discovery, duty-cycle mode). Use for demoing/testing the server stack, Grafana dashboards, or Home Assistant integration without hardware, and for generating test telemetry.
---

# frodas mock device

`tools/mock_device.py` is a software twin of a frodas node — same topics,
same retained/LWT semantics, same control-loop rules (docs/PROTOCOL.md).
One simulated day passes in 5 minutes by default.

## Run (against the local stack)

```bash
# stack must be up; creds are read from server/.env automatically
python3 tools/mock_device.py
python3 tools/mock_device.py --scenario low-battery      # watch tiers escalate
python3 tools/mock_device.py --scenario frost            # trigger frost alerts
python3 tools/mock_device.py --duty-cycle --interval 30  # radio-window behaviour
python3 tools/mock_device.py --discovery                 # + HA MQTT discovery
python3 tools/mock_device.py --duration 120              # bounded run (CI/demo)
```

Remote broker: `--broker <host> --username <u> --password <p>`.
Scenarios: normal, sunny, cloudy, low-battery, frost.

## Behaviours worth knowing

- Ctrl-C / SIGTERM = clean exit (status stays "online" retained — same as a
  device whose radio is just sleeping). SIGKILL = broker fires the LWT
  ("offline"), which is how you test offline alerting.
- It honors retained setpoint commands: publish to
  `frodas/<node>/number/watering_soil_threshold/command` and watch the echo.
- Needs paho-mqtt: run inside `nix develop -c ...` if not installed.

## Typical uses

- Populate Grafana: start stack, run mock 10 min, open the Frodas dashboard.
- Test HA alerts: `--discovery --scenario low-battery`, entities appear via
  MQTT discovery, low-battery automation fires.
- Regression-test protocol changes: the protocol-test skill runs this same
  mock under assertions.
