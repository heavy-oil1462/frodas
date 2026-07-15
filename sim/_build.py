"""Shared build steps for running the frodas firmware under QEMU.

Used by sim/entrypoint.py (inside the container) and tools/test_sim.py
(sandbox/CI integration test) so the two can never drift apart:
config-copy -> secrets -> esphome compile -> 4 MB flash image -> qemu argv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

FLASH_SIZE = 4 * 1024 * 1024


def sync_config(src: Path, dst: Path) -> None:
    """Copy the esphome/ config tree, preserving dst's build cache."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if ".esphome" in item.parts:
            continue
        target = dst / item.relative_to(src)
        if item.is_dir():
            target.mkdir(exist_ok=True)
        else:
            shutil.copy2(item, target)


def write_secrets(config_dir: Path, broker: str, username: str,
                  password: str) -> None:
    (config_dir / "secrets.yaml").write_text(
        f'wifi_ssid: "unused-in-sim"\n'
        f'wifi_password: "unused-in-sim"\n'
        f'mqtt_broker: "{broker}"\n'
        f'mqtt_username: "{username}"\n'
        f'mqtt_password: "{password}"\n'
        f'ota_password: "sim-only"\n'
    )


def compile_firmware(config_dir: Path, node: str,
                     substitutions: dict[str, str] | None = None,
                     esphome_bin: str = "esphome",
                     env: dict[str, str] | None = None) -> Path:
    """Compile sim-greenhouse.yaml; return the factory image path."""
    # -s overrides are global options and must precede the subcommand.
    cmd = [esphome_bin, "-s", "node_name", node]
    for key, value in (substitutions or {}).items():
        cmd += ["-s", key, value]
    cmd += ["compile", str(config_dir / "sim-greenhouse.yaml")]
    print("+", " ".join(cmd), flush=True)
    # A PYTHONPATH from a different python (e.g. the nix devshell's) must not
    # leak into esphome's interpreter — ABI-mismatched imports crash it.
    merged = {**os.environ, **(env or {})}
    merged.pop("PYTHONPATH", None)
    proc = subprocess.run(cmd, env=merged)
    if proc.returncode != 0:
        raise RuntimeError("esphome compile failed")
    matches = sorted(
        (config_dir / ".esphome" / "build" / node).rglob(
            "firmware.factory.bin"))
    if not matches:
        raise RuntimeError("firmware.factory.bin not found after compile")
    return matches[0]


def make_flash_image(factory: Path, out: Path) -> Path:
    """Pad the factory image to a full 4 MB flash for QEMU's MTD drive.

    QEMU mutates this file in place (NVS writes). Always regenerate before
    every boot: a reused image — especially after an unclean QEMU exit — can
    leave NVS in a state that crashloops the firmware during setup().
    """
    data = factory.read_bytes()
    if len(data) > FLASH_SIZE:
        raise RuntimeError(
            f"factory image {len(data)} B exceeds {FLASH_SIZE} B flash")
    out.write_bytes(data + b"\xff" * (FLASH_SIZE - len(data)))
    return out


def qemu_cmd(qemu_bin: Path | str, flash: Path) -> list[str]:
    """argv for booting the flash image on an emulated esp32.

    open_eth + user-mode networking: the guest sees the container/host as
    10.0.2.2, which is where the broker (in tests) and the simulated NTP
    server (always) live from the firmware's point of view.
    """
    return [
        str(qemu_bin), "-machine", "esp32", "-m", "4M",
        "-drive", f"file={flash},if=mtd,format=raw",
        "-nic", "user,model=open_eth",
        "-nographic",
    ]
