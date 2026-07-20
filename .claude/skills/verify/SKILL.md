---
name: verify
description: Run the frodas validation gate (yamllint, esphome config, docker compose config, telegraf/mosquitto/grafana/HA checks, sim contract). Use before every commit, after editing any yaml/json/toml config, or when the user asks to check/validate/lint the repo.
---

# frodas verify gate

Single entry point for all repo validation. Never hand-roll individual lint
commands; this tool is the contract and CI runs exactly the same thing.

```bash
nix develop -c python3 tools/validate.py          # everything
nix develop -c python3 tools/validate.py --list   # list checks
nix develop -c python3 tools/validate.py esphome  # one check
```

Framework and generic checks: the esphome_skills package (flake input
`esphome-skills`). The repo side is tools/project.py (the declaration) and
tools/validate.py (the check dict). Repo yamllint rules: `.yamllint.yaml`.

Canonical doc and landmines (NIX_CONFIG for sandboxes, esphome config vs
compile, running without nix):
https://github.com/heavy-oil1462/esphome-skills/blob/main/skills/verify.md

Related: full firmware C++ build (slow): `nix develop -c esphome compile
esphome/example-greenhouse.yaml`. Protocol behaviour: protocol-test skill.
