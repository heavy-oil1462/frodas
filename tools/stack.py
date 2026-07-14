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

Requires Docker on the host. `init` prefers a local mosquitto_passwd
(devshell) and falls back to running it inside the mosquitto image.
"""

from __future__ import annotations

import json
import secrets as pysecrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import SERVER_DIR, compose_cmd, fail, heading, load_env, ok, run, warn  # noqa: E402

ENV_FILE = SERVER_DIR / ".env"
PASSWD_FILE = SERVER_DIR / "mosquitto/passwd"

MQTT_HOST = "localhost"
MQTT_PORT = 1883
VM_URL = "http://localhost:8428"
GRAFANA_URL = "http://localhost:3000"


def mosquitto_passwd(user: str, password: str, create: bool) -> bool:
    """Add/update a user in the mosquitto password file."""
    PASSWD_FILE.parent.mkdir(parents=True, exist_ok=True)
    create_flag = ["-c"] if create or not PASSWD_FILE.exists() else []
    if shutil.which("mosquitto_passwd"):
        proc = run(["mosquitto_passwd", *create_flag, "-b", str(PASSWD_FILE), user, password])
        if proc.returncode != 0:
            fail(f"mosquitto_passwd: {proc.stderr.strip()}")
            return False
        return True
    if shutil.which("docker"):
        warn("mosquitto_passwd not on PATH, using the docker image instead")
        PASSWD_FILE.touch()
        proc = run([
            "docker", "run", "--rm",
            "-v", f"{PASSWD_FILE.parent.resolve()}:/pw",
            "eclipse-mosquitto:2.0",
            "mosquitto_passwd", *create_flag, "-b", "/pw/passwd", user, password,
        ])
        if proc.returncode != 0:
            fail(f"mosquitto_passwd (docker): {proc.stderr.strip()}")
            return False
        return True
    fail("need mosquitto_passwd (nix develop) or docker to hash the password")
    return False


def cmd_init() -> int:
    if ENV_FILE.exists():
        warn(f"{ENV_FILE} already exists — leaving it untouched")
        env = load_env(ENV_FILE)
    else:
        env = {
            "MQTT_USER": "frodas",
            "MQTT_PASSWORD": pysecrets.token_urlsafe(16),
            "GRAFANA_ADMIN_USER": "admin",
            "GRAFANA_ADMIN_PASSWORD": pysecrets.token_urlsafe(16),
        }
        ENV_FILE.write_text(
            "".join(f"{key}={value}\n" for key, value in env.items())
        )
        ok(f"wrote {ENV_FILE} with generated passwords")

    if not mosquitto_passwd(env["MQTT_USER"], env["MQTT_PASSWORD"], create=True):
        return 1
    ok(f"wrote {PASSWD_FILE} for user '{env['MQTT_USER']}'")
    print(
        "\nNext steps:\n"
        "  * put MQTT credentials into esphome/secrets.yaml\n"
        "  * python3 tools/stack.py up\n"
        "  * python3 tools/stack.py smoke"
    )
    return 0


def ensure_initialized() -> dict[str, str]:
    if not ENV_FILE.exists() or not PASSWD_FILE.exists():
        fail("stack not initialized — run: python3 tools/stack.py init")
        sys.exit(1)
    return load_env(ENV_FILE)


def cmd_up() -> int:
    ensure_initialized()
    proc = subprocess.run([*compose_cmd(), "up", "-d"], cwd=SERVER_DIR)
    if proc.returncode != 0:
        return proc.returncode
    ok("stack started")
    print(f"Grafana:         {GRAFANA_URL}")
    print(f"VictoriaMetrics: {VM_URL}")
    print(f"MQTT:            {MQTT_HOST}:{MQTT_PORT}")
    return 0


def cmd_down() -> int:
    return subprocess.run([*compose_cmd(), "down"], cwd=SERVER_DIR).returncode


def cmd_status() -> int:
    return subprocess.run([*compose_cmd(), "ps"], cwd=SERVER_DIR).returncode


def cmd_logs(service: str | None) -> int:
    args = [*compose_cmd(), "logs", "--tail", "100"]
    if service:
        args.append(service)
    return subprocess.run(args, cwd=SERVER_DIR).returncode


def http_json(url: str, timeout: int = 5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def cmd_smoke() -> int:
    env = ensure_initialized()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _lib import mqtt_client

    failures = 0

    heading("1. MQTT round trip")
    received: list[str] = []
    client = mqtt_client("frodas-smoke", env["MQTT_USER"], env["MQTT_PASSWORD"])
    client.on_message = lambda _c, _u, msg: received.append(msg.payload.decode())
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
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
    client.publish("frodas/smoketest/sensor/battery_voltage/state", marker, retain=True)
    query = urllib.parse.quote('battery_voltage_value{node="smoketest"}')
    landed = False
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            result = http_json(f"{VM_URL}/api/v1/query?query={query}")
            samples = result.get("data", {}).get("result", [])
            if any(s["value"][1] == marker for s in samples):
                landed = True
                break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    if landed:
        ok(f"battery_voltage_value{{node=\"smoketest\"}} = {marker} in VictoriaMetrics")
    else:
        fail("smoke metric did not land in VictoriaMetrics within 45 s "
             "(check: python3 tools/stack.py logs telegraf)")
        failures += 1

    heading("3. Grafana health")
    try:
        health = http_json(f"{GRAFANA_URL}/api/health")
        if health.get("database") == "ok":
            ok(f"grafana healthy (version {health.get('version', '?')})")
        else:
            fail(f"grafana unhealthy: {health}")
            failures += 1
    except (urllib.error.URLError, OSError) as err:
        fail(f"grafana unreachable: {err}")
        failures += 1

    # cleanup retained smoke topic
    client.publish("frodas/smoketest/sensor/battery_voltage/state", "", retain=True)
    client.loop_stop()
    client.disconnect()

    heading("summary")
    (ok if failures == 0 else fail)(f"smoke test: {failures} failure(s)")
    return 1 if failures else 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command = argv[0]
    if command == "init":
        return cmd_init()
    if command == "up":
        return cmd_up()
    if command == "down":
        return cmd_down()
    if command == "status":
        return cmd_status()
    if command == "logs":
        return cmd_logs(argv[1] if len(argv) > 1 else None)
    if command == "smoke":
        return cmd_smoke()
    if command == "passwd":
        if len(argv) != 3:
            fail("usage: stack.py passwd <user> <password>")
            return 2
        return 0 if mosquitto_passwd(argv[1], argv[2], create=False) else 1
    fail(f"unknown command: {command}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
