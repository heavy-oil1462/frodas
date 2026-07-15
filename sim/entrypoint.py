#!/usr/bin/env python3
"""frodas sim container entrypoint.

Turns environment variables into a running simulated greenhouse:

  1. Copies the repo's esphome/ configs into the cache volume and writes
     secrets.yaml from MQTT_* env vars (the broker hostname is resolved to an
     IP first — QEMU user-mode networking cannot use container-only names
     like host.containers.internal).
  2. Compiles esphome/sim-greenhouse.yaml — the REAL firmware, esp32 target.
     Slow the first time (~minutes); afterwards the PlatformIO/build caches
     in the /cache volume make it quick. Recompiles only when inputs change.
  3. Pads the factory image to a 4 MB flash image and boots it under
     Espressif QEMU (machine esp32, open_eth NIC, user-mode networking).
  4. Runs the control panel (webui.py): http on :8080, simulated NTP on
     udp/123 for the firmware's clock.

Environment:
  MQTT_HOST (required), MQTT_PORT=1883, MQTT_USER, MQTT_PASSWORD
  MQTT_ROOT=frodas, SIM_NODE=frodas-sim, SIM_TIMEZONE=Europe/Stockholm
  SIM_HTTP_PORT=8080, SIM_NTP_PORT=123
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
from sim import _build  # noqa: E402

APP = Path("/app")
CACHE = Path("/cache")
CONFIG = CACHE / "config"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        sys.exit(f"error: {name} must be set (podman run -e {name}=...)")
    return value


def resolve(host: str) -> str:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except socket.gaierror:
        sys.exit(f"error: cannot resolve MQTT_HOST={host!r}")


def main() -> int:
    broker = env("MQTT_HOST")
    broker_ip = resolve(broker)
    node = env("SIM_NODE", "frodas-sim")
    timezone = env("SIM_TIMEZONE", "Europe/Stockholm")
    print(f"frodas sim: node={node} broker={broker} ({broker_ip}) "
          f"tz={timezone}", flush=True)

    _build.sync_config(APP / "esphome", CONFIG)
    _build.write_secrets(CONFIG, broker_ip, env("MQTT_USER", ""),
                         env("MQTT_PASSWORD", ""))
    try:
        factory = _build.compile_firmware(
            CONFIG, node,
            substitutions={
                "timezone": timezone,
                "mqtt_port": env("MQTT_PORT", "1883"),
                "mqtt_root": env("MQTT_ROOT", "frodas"),
            },
            env={**os.environ, "PLATFORMIO_CORE_DIR": str(CACHE / "pio")})
        flash = _build.make_flash_image(factory, CACHE / "flash.bin")
    except RuntimeError as err:
        sys.exit(f"error: {err}")

    qemu_bin = (Path(Path("/opt/qemu-path").read_text().strip())
                / "bin" / "qemu-system-xtensa")
    qemu = subprocess.Popen(_build.qemu_cmd(qemu_bin, flash))
    webui = subprocess.Popen([
        sys.executable, str(APP / "sim" / "webui.py"),
        "--broker", broker,
        "--node", node,
        "--timezone", timezone,
    ])
    procs = {"qemu (firmware)": qemu, "webui": webui}

    def shutdown(*_):
        for p in procs.values():
            p.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # If either process exits, take the whole container down with it.
    while all(p.poll() is None for p in procs.values()):
        time.sleep(1)
    exited = next(n for n, p in procs.items() if p.poll() is not None)
    code = procs[exited].poll()
    print(f"frodas sim: {exited} exited with {code}; stopping", flush=True)
    shutdown()
    for p in procs.values():
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    return code or 1


if __name__ == "__main__":
    sys.exit(main())
