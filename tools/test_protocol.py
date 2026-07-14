#!/usr/bin/env python3
"""frodas protocol integration test — no Docker, no hardware.

Boots a throwaway authenticated mosquitto on an ephemeral port, runs
tools/mock_device.py against it, and asserts the MQTT contract from
docs/PROTOCOL.md:

    1. retained availability: <root>/<node>/status = "online"
    2. telemetry is numeric and RETAINED (a late subscriber still gets it)
    3. setpoint command -> retained state echo (number/command round trip)
    4. enable-switch command -> state echo + behavioral effect
    5. ungraceful death (SIGKILL) -> broker publishes the LWT "offline"

Run inside the devshell (needs mosquitto + mosquitto_passwd + paho-mqtt):
    nix develop -c python3 tools/test_protocol.py

Exit code 0 = all assertions passed. This is part of the validation gate
and runs in CI.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import REPO_ROOT, fail, heading, mqtt_client, ok, require  # noqa: E402

NODE = "prototest"
ROOT = "frodas"
PREFIX = f"{ROOT}/{NODE}"
USER, PASSWORD = "prototest", "prototest-pw"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Collector:
    """MQTT test client collecting every message with its retain flag."""

    def __init__(self, port: int, client_id: str):
        self.messages: dict[str, tuple[str, bool]] = {}
        self.client = mqtt_client(client_id, USER, PASSWORD)
        self.client.on_message = self._on_message
        self.client.connect("127.0.0.1", port, keepalive=30)
        self.client.subscribe(f"{ROOT}/#")
        self.client.loop_start()

    def _on_message(self, _client, _userdata, msg) -> None:
        self.messages[msg.topic] = (msg.payload.decode(), bool(msg.retain))

    def wait_for(self, topic: str, predicate=lambda _v: True, timeout: float = 10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if topic in self.messages and predicate(self.messages[topic][0]):
                return self.messages[topic]
            time.sleep(0.05)
        return None

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.client.publish(topic, payload, retain=retain)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def main() -> int:
    require("mosquitto")
    require("mosquitto_passwd")

    failures = 0

    def check(condition: bool, label: str) -> None:
        nonlocal failures
        if condition:
            ok(label)
        else:
            fail(label)
            failures += 1

    with tempfile.TemporaryDirectory(prefix="frodas-prototest-") as tmp:
        tmp_path = Path(tmp)
        port = free_port()

        passwd = tmp_path / "passwd"
        passwd.touch()
        os.chmod(passwd, 0o700)
        subprocess.run(
            ["mosquitto_passwd", "-b", str(passwd), USER, PASSWORD],
            check=True, capture_output=True,
        )
        conf = tmp_path / "mosquitto.conf"
        conf.write_text(
            f"listener {port} 127.0.0.1\n"
            "allow_anonymous false\n"
            f"password_file {passwd}\n"
            "persistence false\n"
            # when the test runs as root (sandboxes, containers) mosquitto
            # would try to drop privileges to a user that may not exist
            + ("user root\n" if os.geteuid() == 0 else "")
        )

        heading("starting throwaway broker + mock device")
        broker = subprocess.Popen(
            ["mosquitto", "-c", str(conf)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        mock = None
        try:
            time.sleep(0.5)
            check(broker.poll() is None, f"mosquitto up on 127.0.0.1:{port}")

            mock_log = (tmp_path / "mock.log").open("w")
            mock = subprocess.Popen(
                [
                    sys.executable, str(REPO_ROOT / "tools/mock_device.py"),
                    "--broker", "127.0.0.1", "--port", str(port),
                    "--username", USER, "--password", PASSWORD,
                    "--node", NODE, "--root", ROOT,
                    "--interval", "1", "--keepalive", "2",
                ],
                stdout=mock_log, stderr=subprocess.STDOUT,
            )

            watcher = Collector(port, "prototest-watcher")

            heading("1. availability")
            status = watcher.wait_for(f"{PREFIX}/status", lambda v: v == "online")
            check(status is not None, "status topic went 'online'")

            heading("2. retained telemetry")
            batt = watcher.wait_for(f"{PREFIX}/sensor/battery_voltage/state")
            check(batt is not None, "battery_voltage telemetry arrived")
            if batt:
                value, _ = batt
                check(10.0 < float(value) < 15.0,
                      f"battery_voltage is a sane float ({value} V)")
            # retention: a NEW subscriber must immediately get the state
            time.sleep(1.5)
            late = Collector(port, "prototest-late")
            late_batt = late.wait_for(f"{PREFIX}/sensor/battery_voltage/state",
                                      timeout=5)
            check(late_batt is not None and late_batt[1],
                  "late subscriber receives telemetry with retain flag set")
            late_status = late.wait_for(f"{PREFIX}/status", timeout=5)
            check(late_status is not None and late_status[1],
                  "late subscriber receives retained availability")
            late.stop()

            heading("3. setpoint round trip (retained command -> retained state)")
            watcher.publish(f"{PREFIX}/number/watering_soil_threshold/command",
                            "42", retain=True)
            echo = watcher.wait_for(
                f"{PREFIX}/number/watering_soil_threshold/state",
                lambda v: v == "42")
            check(echo is not None, "number command echoed on state topic")

            heading("4. enable switch round trip")
            watcher.publish(f"{PREFIX}/switch/watering_enabled/command",
                            "OFF", retain=True)
            echo = watcher.wait_for(f"{PREFIX}/switch/watering_enabled/state",
                                    lambda v: v == "OFF")
            check(echo is not None, "switch command echoed on state topic")
            # cleanup the retained commands so reruns start clean
            watcher.publish(f"{PREFIX}/number/watering_soil_threshold/command",
                            "", retain=True)
            watcher.publish(f"{PREFIX}/switch/watering_enabled/command",
                            "", retain=True)

            heading("5. LWT on ungraceful death")
            mock.send_signal(signal.SIGKILL)
            mock.wait(timeout=5)
            lwt = watcher.wait_for(f"{PREFIX}/status",
                                   lambda v: v == "offline", timeout=15)
            check(lwt is not None, "broker published LWT 'offline' after SIGKILL")

            watcher.stop()
            if failures:
                print("\n[mock device log]")
                print((tmp_path / "mock.log").read_text())
        finally:
            for proc in (mock, broker):
                if proc and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

    heading("summary")
    (ok if failures == 0 else fail)(f"protocol test: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
