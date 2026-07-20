#!/usr/bin/env python3
"""frodas validation gate - run every check the repo must keep green.

Usage:
    python3 tools/validate.py                 # everything
    python3 tools/validate.py yaml esphome    # a subset
    python3 tools/validate.py --list          # show available checks

Checks (framework and generic checks: esphome_skills):
    yaml       yamllint over the whole repo (.yamllint.yaml rules)
    esphome    `esphome config` on the example and sim compositions - the
               base is transport-agnostic and only validates composed with a
               radio package (auto-provisions esphome/secrets.yaml)
    compose    Docker Compose file parses (`docker compose config -q`)
    telegraf   telegraf.conf is valid TOML with the expected inputs/outputs
    mosquitto  mosquitto.conf enforces auth + persistence
    grafana    provisioning YAML + dashboard JSON parse, datasource UIDs match
    ha         Home Assistant package/blueprint/dashboard YAML parses
    sim        project injection keys match sim-sensors.yaml topics; the sim
               container staging sources exist
    python     tools/*.py byte-compile

Intended entry points: `.claude/skills/verify`, CI, and pre-commit.
Run inside the devshell (`nix develop`) so all binaries are present.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import PROJECT  # noqa: E402

from esphome_skills import checks, validate  # noqa: E402

CHECKS = {
    "yaml": checks.check_yaml,
    "esphome": checks.check_esphome,
    "compose": checks.check_compose,
    "telegraf": checks.check_telegraf,
    "mosquitto": checks.check_mosquitto,
    "grafana": checks.check_grafana,
    "ha": checks.check_ha,
    "sim": checks.check_sim,
    "python": checks.check_python,
}

if __name__ == "__main__":
    sys.exit(validate.main(PROJECT, CHECKS))
