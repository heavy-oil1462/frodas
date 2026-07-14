"""Shared helpers for the frodas tools. Not a public API."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "server"
ESPHOME_DIR = REPO_ROOT / "esphome"

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}✔{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}✘{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}⚠{RESET} {msg}")


def heading(msg: str) -> None:
    print(f"\n{BOLD}── {msg} {'─' * max(0, 60 - len(msg))}{RESET}")


def run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """Run a command, capturing output."""
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def require(binary: str, hint: str = "enter the devshell: nix develop") -> str:
    """Resolve a binary on PATH or exit with a hint."""
    path = shutil.which(binary)
    if not path:
        fail(f"'{binary}' not found on PATH — {hint}")
        sys.exit(2)
    return path


def compose_cmd() -> list[str]:
    """Find a Docker Compose CLI: `docker compose` (v2) or `docker-compose`."""
    if shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True
        )
        if probe.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    fail("no Docker Compose CLI found (docker compose / docker-compose)")
    sys.exit(2)


def load_env(path: Path) -> dict[str, str]:
    """Parse a docker-compose style .env file."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def mqtt_client(client_id: str, username: str | None = None,
                password: str | None = None):
    """Create a paho-mqtt client, compatible with paho 1.x and 2.x."""
    import paho.mqtt.client as mqtt

    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
    except (AttributeError, TypeError):  # paho 1.x
        client = mqtt.Client(client_id=client_id)
    if username:
        client.username_pw_set(username, password)
    return client
