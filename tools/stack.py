#!/usr/bin/env python3
"""frodas server stack management (mosquitto + telegraf + VictoriaMetrics + Grafana).

Usage:
    python3 tools/stack.py init      # generate server/.env + mosquitto passwd
    python3 tools/stack.py up        # start the stack (docker compose up -d)
    python3 tools/stack.py smoke     # end-to-end health check (see below)
    python3 tools/stack.py status    # compose ps
    python3 tools/stack.py logs [service]
    python3 tools/stack.py down
    python3 tools/stack.py passwd <user> <password>   # add/update an MQTT user

`smoke` verifies the whole telemetry path:
    1. MQTT round trip (authenticated publish + subscribe)
    2. publishes a fake reading on frodas/smoketest/sensor/battery_voltage/state
    3. polls VictoriaMetrics until battery_voltage_value{node="smoketest"} lands
    4. checks the Grafana health endpoint
    5. cleans up the retained smoke-test topic

Plumbing: esphome_skills.stack. Requires Docker on the host. `init` prefers
a local mosquitto_passwd (devshell) and falls back to running it inside the
mosquitto image.
"""

from __future__ import annotations

import secrets as pysecrets
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import PROJECT  # noqa: E402

from esphome_skills import stack  # noqa: E402
from esphome_skills.lib import fail, heading, mqtt_client, ok  # noqa: E402

VM_URL = "http://localhost:8428"
GRAFANA_URL = "http://localhost:3000"


def smoke(project, env) -> int:
    failures = 0

    heading("1. MQTT round trip")
    received: list[str] = []
    client = mqtt_client("frodas-smoke", env["MQTT_USER"],
                         env["MQTT_PASSWORD"])
    client.on_message = \
        lambda _c, _u, msg: received.append(msg.payload.decode())
    try:
        client.connect(stack.MQTT_HOST, stack.MQTT_PORT, keepalive=10)
    except OSError as err:
        fail(f"cannot connect to broker: {err}")
        return 1
    client.loop_start()
    client.subscribe("frodas/smoketest/echo")
    time.sleep(0.5)
    client.publish("frodas/smoketest/echo", "ping")
    deadline = time.time() + 5
    while time.time() < deadline and "ping" not in received:
        time.sleep(0.1)
    if "ping" in received:
        ok("authenticated publish/subscribe round trip")
    else:
        fail("no echo received within 5 s")
        failures += 1

    heading("2. telemetry -> VictoriaMetrics")
    marker = str(round(12.0 + (time.time() % 1), 3))
    client.publish("frodas/smoketest/sensor/battery_voltage/state", marker,
                   retain=True)
    query = urllib.parse.quote('battery_voltage_value{node="smoketest"}')
    landed = False
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            result = stack.http_json(f"{VM_URL}/api/v1/query?query={query}")
            samples = result.get("data", {}).get("result", [])
            if any(s["value"][1] == marker for s in samples):
                landed = True
                break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    if landed:
        ok(f"battery_voltage_value{{node=\"smoketest\"}} = {marker} "
           "in VictoriaMetrics")
    else:
        fail("smoke metric did not land in VictoriaMetrics within 45 s "
             "(check: python3 tools/stack.py logs telegraf)")
        failures += 1

    heading("3. Grafana health")
    try:
        health = stack.http_json(f"{GRAFANA_URL}/api/health")
        if health.get("database") == "ok":
            ok(f"grafana healthy (version {health.get('version', '?')})")
        else:
            fail(f"grafana unhealthy: {health}")
            failures += 1
    except (urllib.error.URLError, OSError) as err:
        fail(f"grafana unreachable: {err}")
        failures += 1

    # cleanup retained smoke topic
    client.publish("frodas/smoketest/sensor/battery_voltage/state", "",
                   retain=True)
    client.loop_stop()
    client.disconnect()

    heading("summary")
    (ok if failures == 0 else fail)(f"smoke test: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(stack.main(
        PROJECT,
        smoke=smoke,
        extra_env=lambda: {
            "GRAFANA_ADMIN_USER": "admin",
            "GRAFANA_ADMIN_PASSWORD": pysecrets.token_urlsafe(16),
        },
        endpoints={
            "Grafana": GRAFANA_URL,
            "VictoriaMetrics": VM_URL,
        },
        usage=__doc__,
    ))
