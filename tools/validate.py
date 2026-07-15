#!/usr/bin/env python3
"""frodas validation gate — run every check the repo must keep green.

Usage:
    python3 tools/validate.py                 # everything
    python3 tools/validate.py yaml esphome    # a subset
    python3 tools/validate.py --list          # show available checks

Checks:
    yaml       yamllint over the whole repo (.yamllint.yaml rules)
    esphome    `esphome config` on the example and sim compositions — the
               base is transport-agnostic and only validates composed with a
               radio package (auto-provisions esphome/secrets.yaml)
    compose    Docker Compose file parses (`docker compose config -q`)
    telegraf   telegraf.conf is valid TOML with the expected inputs/outputs
    mosquitto  mosquitto.conf enforces auth + persistence
    grafana    provisioning YAML + dashboard JSON parse, datasource UIDs match
    ha         Home Assistant package/blueprint/dashboard YAML parses
    sim        web UI injection keys match sim-sensors.yaml topics;
               sim/Containerfile only COPYs paths that exist
    python     tools/*.py + sim/*.py byte-compile

Intended entry points: `.claude/skills/validate`, CI, and pre-commit.
Run inside the devshell (`nix develop`) so all binaries are present.
"""

from __future__ import annotations

import json
import py_compile
import shutil
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import (  # noqa: E402
    ESPHOME_DIR,
    REPO_ROOT,
    SERVER_DIR,
    fail,
    heading,
    ok,
    run,
    warn,
)


def check_yaml() -> bool:
    if not shutil.which("yamllint"):
        fail("yamllint not on PATH — enter the devshell: nix develop")
        return False
    proc = run(["yamllint", "--strict", "."])
    if proc.returncode != 0:
        fail("yamllint:")
        print(proc.stdout or proc.stderr)
        return False
    ok("yamllint clean")
    return True


def check_esphome() -> bool:
    esphome = shutil.which("esphome") or str(REPO_ROOT / ".venv/bin/esphome")
    if not Path(esphome).exists():
        fail("esphome not found — enter the devshell: nix develop")
        return False

    secrets = ESPHOME_DIR / "secrets.yaml"
    if not secrets.exists():
        shutil.copy(ESPHOME_DIR / "secrets.yaml.example", secrets)
        warn("provisioned esphome/secrets.yaml from example (placeholders)")

    good = True
    for config in ("example-greenhouse.yaml", "sim-greenhouse.yaml"):
        proc = run([esphome, "config", str(ESPHOME_DIR / config)], timeout=300)
        if proc.returncode != 0:
            fail(f"esphome config {config}:")
            tail = (proc.stdout + proc.stderr).splitlines()[-30:]
            print("\n".join(tail))
            good = False
        else:
            ok(f"esphome config {config}")
    return good


def check_compose() -> bool:
    if shutil.which("docker"):
        probe = run(["docker", "compose", "version"])
        cmd = ["docker", "compose"] if probe.returncode == 0 else None
    else:
        cmd = None
    if cmd is None and shutil.which("docker-compose"):
        cmd = ["docker-compose"]
    if cmd is None:
        fail("no docker compose CLI — enter the devshell: nix develop")
        return False

    env_file = SERVER_DIR / ".env"
    env_arg = ["--env-file", ".env" if env_file.exists() else ".env.example"]
    proc = run([*cmd, *env_arg, "config", "--quiet"], cwd=SERVER_DIR)
    if proc.returncode != 0:
        fail("docker compose config:")
        print(proc.stdout or proc.stderr)
        return False
    ok("docker compose config valid")
    return True


def check_telegraf() -> bool:
    path = SERVER_DIR / "telegraf/telegraf.conf"
    try:
        conf = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as err:
        fail(f"telegraf.conf TOML parse: {err}")
        return False
    good = True
    if "outputs" not in conf or "influxdb" not in conf["outputs"]:
        fail("telegraf.conf: missing outputs.influxdb")
        good = False
    consumers = conf.get("inputs", {}).get("mqtt_consumer", [])
    if len(consumers) < 3:
        fail("telegraf.conf: expected >= 3 mqtt_consumer inputs")
        good = False
    for consumer in consumers:
        if not consumer.get("topics"):
            fail("telegraf.conf: mqtt_consumer without topics")
            good = False
    if good:
        ok(f"telegraf.conf valid ({len(consumers)} mqtt consumers)")
    return good


def check_mosquitto() -> bool:
    path = SERVER_DIR / "mosquitto/mosquitto.conf"
    text = path.read_text()
    lines = {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    good = True
    for required in ("allow_anonymous false", "persistence true"):
        if required not in lines:
            fail(f"mosquitto.conf: missing '{required}'")
            good = False
    if not any(line.startswith("password_file ") for line in lines):
        fail("mosquitto.conf: missing password_file")
        good = False
    if good:
        ok("mosquitto.conf enforces auth + persistence")
    return good


def check_grafana() -> bool:
    import yaml

    good = True
    datasource_uids: set[str] = set()
    for prov in (SERVER_DIR / "grafana/provisioning").rglob("*.yaml"):
        try:
            doc = yaml.safe_load(prov.read_text())
        except yaml.YAMLError as err:
            fail(f"{prov.name}: {err}")
            good = False
            continue
        for ds in doc.get("datasources", []) or []:
            datasource_uids.add(ds.get("uid"))
    for dash_path in (SERVER_DIR / "grafana/dashboards").glob("*.json"):
        try:
            dash = json.loads(dash_path.read_text())
        except json.JSONDecodeError as err:
            fail(f"{dash_path.name}: {err}")
            good = False
            continue
        panels = dash.get("panels", [])
        if not panels:
            fail(f"{dash_path.name}: no panels")
            good = False
        used_uids = {
            panel.get("datasource", {}).get("uid")
            for panel in panels
            if isinstance(panel.get("datasource"), dict)
        }
        unknown = used_uids - datasource_uids - {None}
        unknown = {u for u in unknown if not str(u).startswith("$")}
        if unknown:
            fail(f"{dash_path.name}: panels reference unknown datasource uid(s) {unknown}")
            good = False
        if good:
            ok(f"grafana {dash_path.name}: {len(panels)} panels, datasource uids match")
    if not datasource_uids:
        fail("no grafana datasources provisioned")
        good = False
    return good


def check_ha() -> bool:
    import yaml

    class HALoader(yaml.SafeLoader):
        """Tolerate HA-specific tags (!input, !secret, !include...)."""

    def _ignore_tag(loader, tag_suffix, node):  # noqa: ANN001
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    HALoader.add_multi_constructor("!", _ignore_tag)

    good = True
    ha_dir = REPO_ROOT / "homeassistant"
    files = sorted(ha_dir.rglob("*.yaml"))
    if not files:
        fail("no homeassistant yaml files found")
        return False
    for path in files:
        try:
            yaml.load(path.read_text(), Loader=HALoader)
            ok(f"ha {path.relative_to(ha_dir)} parses")
        except yaml.YAMLError as err:
            fail(f"ha {path.relative_to(ha_dir)}: {err}")
            good = False
    return good


def check_python() -> bool:
    good = True
    files = sorted((REPO_ROOT / "tools").glob("*.py")) + sorted(
        (REPO_ROOT / "sim").glob("*.py"))
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as err:
            fail(f"{path.relative_to(REPO_ROOT)}: {err}")
            good = False
    if good:
        ok(f"{len(files)} python files byte-compile (tools/ + sim/)")
    return good


def check_sim() -> bool:
    """Cross-artifact contract: web UI <-> sim-sensors.yaml <-> Containerfile."""
    import re

    good = True

    # Injection keys offered by the web UI must be exactly the sim/<key>
    # topics the firmware's mqtt_subscribe sensors listen on.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "frodas_sim_webui", REPO_ROOT / "sim" / "webui.py")
    webui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(webui)
    ui_keys = set(webui.INJECTIONS)

    sensors_yaml = (ESPHOME_DIR / "packages" / "sim-sensors.yaml").read_text()
    fw_keys = set(re.findall(
        r"topic:\s*\$\{mqtt_root\}/\$\{node_name\}/sim/(\S+)", sensors_yaml))
    if ui_keys != fw_keys:
        fail(f"sim injection keys drifted: webui={sorted(ui_keys)} "
             f"firmware={sorted(fw_keys)}")
        good = False
    else:
        ok(f"web UI injection keys match sim-sensors.yaml ({len(ui_keys)})")

    # Presets must only reference known keys (or 'time').
    bad_presets = {
        name for name, preset in webui.PRESETS.items()
        if set(preset) - ui_keys - {"time"}
    }
    if bad_presets:
        fail(f"web UI presets use unknown keys: {sorted(bad_presets)}")
        good = False
    else:
        ok(f"web UI presets reference valid keys ({len(webui.PRESETS)})")

    # Containerfile must only COPY repo paths that exist.
    containerfile = (REPO_ROOT / "sim" / "Containerfile").read_text()
    for line in containerfile.splitlines():
        if line.startswith("COPY ") and "--from=" not in line:
            sources = line.split()[1:-1]
            for src in sources:
                if not (REPO_ROOT / src.rstrip("/")).exists():
                    fail(f"Containerfile COPYs missing path: {src}")
                    good = False
    if good:
        ok("Containerfile COPY sources exist")
    return good


CHECKS = {
    "yaml": check_yaml,
    "esphome": check_esphome,
    "compose": check_compose,
    "telegraf": check_telegraf,
    "mosquitto": check_mosquitto,
    "grafana": check_grafana,
    "ha": check_ha,
    "sim": check_sim,
    "python": check_python,
}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        print("\n".join(CHECKS))
        return 0
    selected = args or list(CHECKS)
    unknown = set(selected) - set(CHECKS)
    if unknown:
        fail(f"unknown checks: {', '.join(sorted(unknown))} (see --list)")
        return 2

    results: dict[str, bool] = {}
    for name in selected:
        heading(name)
        results[name] = CHECKS[name]()

    heading("summary")
    for name, passed in results.items():
        (ok if passed else fail)(name)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
