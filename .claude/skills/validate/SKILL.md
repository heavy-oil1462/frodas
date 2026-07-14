---
name: validate
description: Run the frodas validation gate (yamllint, esphome config, docker compose config, telegraf/mosquitto/grafana/HA checks). Use before every commit, after editing any yaml/json/toml config, or when the user asks to check/validate/lint the repo.
---

# frodas validation gate

Single entry point for all repo validation. Never hand-roll individual lint
commands — this tool is the contract and CI runs exactly the same thing.

## Run

```bash
nix develop -c python3 tools/validate.py          # everything
nix develop -c python3 tools/validate.py --list   # list checks
nix develop -c python3 tools/validate.py esphome  # one check (see --list)
```

In sandboxes where nix lacks a build group, export first:

```bash
export NIX_CONFIG="experimental-features = nix-command flakes
build-users-group =
sandbox = false"
```

Without nix: needs `yamllint`, `esphome` (or `.venv/bin/esphome`), a docker
compose CLI, and python3.11+ with pyyaml on PATH.

## Interpreting failures

- `esphome config` failures print the last 30 lines — the offending key is
  usually named. Substitution errors surface as unknown-value errors at the
  point of use, not at the substitution definition.
- `yamllint` failures include file:line. Repo rules live in `.yamllint.yaml`.
- Compose failures: run `docker compose --env-file .env.example config` in
  `server/` for the full message.
- Missing-binary failures mean you are outside the devshell.

## Related

- Full firmware C++ build (slow, ~5 min + toolchain download):
  `nix develop -c esphome compile esphome/example-greenhouse.yaml`
  (if platformio's bwrap fails in a sandbox: `.venv/bin/esphome compile ...`
  after `python3 -m venv .venv && .venv/bin/pip install esphome`)
- Protocol/broker behaviour: use the `protocol-test` skill.
