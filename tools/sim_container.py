#!/usr/bin/env python3
"""frodas sim container manager — a simulated greenhouse against YOUR stack.

Builds and runs the simulation container (sim/Containerfile): the real
firmware compiled for esp32 and executed under Espressif QEMU, plus the
control panel web UI for injecting sensor values and time of day. Point it
at your own MQTT broker and the node shows up in Home Assistant via MQTT
discovery, indistinguishable from hardware.

    python3 tools/sim_container.py build
    python3 tools/sim_container.py run --broker 192.168.1.10 \
        --username frodas --password secret
    python3 tools/sim_container.py logs
    python3 tools/sim_container.py stop

Then open http://localhost:8080 and drag sliders.

Prefers podman, falls back to docker. The first `run` compiles the firmware
inside the container (~minutes); the named volume <name>-cache makes
subsequent starts fast. If your broker runs on the same machine, pass
--broker host.containers.internal (podman) or the machine's LAN IP.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import REPO_ROOT, fail, ok  # noqa: E402

IMAGE = "frodas-sim"


def engine() -> str:
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    fail("neither podman nor docker found on PATH")
    sys.exit(2)


def sh(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


def cmd_build(args) -> int:
    return sh([engine(), "build", "-f", "sim/Containerfile",
               "-t", f"{IMAGE}:latest", str(REPO_ROOT)])


def cmd_run(args) -> int:
    eng = engine()
    if eng == "docker":  # podman has --replace; docker needs an explicit rm
        subprocess.run([eng, "rm", "-f", args.name], capture_output=True)
    rc = sh([
        eng, "run", "-d", "--name", args.name,
        *(["--replace"] if eng == "podman" else []),
        "-p", f"{args.http_port}:8080",
        "-v", f"{args.name}-cache:/cache",
        "-e", f"MQTT_HOST={args.broker}",
        "-e", f"MQTT_PORT={args.port}",
        "-e", f"MQTT_USER={args.username or ''}",
        "-e", f"MQTT_PASSWORD={args.password or ''}",
        "-e", f"MQTT_ROOT={args.root}",
        "-e", f"SIM_NODE={args.node}",
        "-e", f"SIM_TIMEZONE={args.timezone}",
        f"{IMAGE}:latest",
    ])
    if rc == 0:
        ok(f"simulator starting — control panel: http://localhost:{args.http_port}")
        ok(f"first start compiles the firmware; follow along: "
           f"python3 tools/sim_container.py logs")
    return rc


def cmd_logs(args) -> int:
    return sh([engine(), "logs", "-f", args.name])


def cmd_stop(args) -> int:
    return sh([engine(), "rm", "-f", args.name])


def cmd_status(args) -> int:
    return sh([engine(), "ps", "--filter", f"name={args.name}"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="build the container image")
    run_p = sub.add_parser("run", help="start the simulator (detached)")
    run_p.add_argument("--broker", required=True,
                       help="your MQTT broker (IP/hostname; use "
                            "host.containers.internal for the podman host)")
    run_p.add_argument("--port", default="1883")
    run_p.add_argument("--username")
    run_p.add_argument("--password")
    run_p.add_argument("--root", default="frodas", help="MQTT topic root")
    run_p.add_argument("--node", default="frodas-sim")
    run_p.add_argument("--timezone", default="Europe/Stockholm")
    run_p.add_argument("--http-port", default="8080",
                       help="host port for the control panel")
    sub.add_parser("logs", help="follow container logs (firmware serial too)")
    sub.add_parser("stop", help="stop and remove the container")
    sub.add_parser("status", help="show container state")
    for p in sub.choices.values():
        p.add_argument("--name", default="frodas-sim",
                       help="container name (default: frodas-sim)")

    args = parser.parse_args()
    return {"build": cmd_build, "run": cmd_run, "logs": cmd_logs,
            "stop": cmd_stop, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
