---
name: server-stack
description: Bring up, check, or manage the frodas server stack (mosquitto, telegraf, VictoriaMetrics, Grafana via docker compose). Use when the user wants the server running, MQTT credentials created, smoke tests, logs, or is debugging why telemetry isn't reaching Grafana.
---

# frodas server stack

Everything goes through `tools/stack.py` — do not run raw `docker compose` or
craft ad-hoc mosquitto commands.

## Commands

```bash
python3 tools/stack.py init    # once: generate server/.env + mosquitto passwd
python3 tools/stack.py up      # docker compose up -d (requires init)
python3 tools/stack.py smoke   # end-to-end: MQTT round trip -> VM metric -> Grafana health
python3 tools/stack.py status
python3 tools/stack.py logs [mosquitto|telegraf|victoriametrics|grafana]
python3 tools/stack.py down
python3 tools/stack.py passwd <user> <password>   # extra MQTT users
```

Needs Docker on the host. `init` needs `mosquitto_passwd` (devshell:
`nix develop -c python3 tools/stack.py init`) or falls back to the docker
image. Generated secrets land in `server/.env` (gitignored).

## Endpoints after `up`

- Grafana: http://localhost:3000 (creds in `server/.env`), dashboard
  auto-provisioned in folder "Frodas"
- VictoriaMetrics: http://localhost:8428 (PromQL at /api/v1/query)
- MQTT: localhost:1883 (authenticated, no anonymous)

## Debugging the telemetry path

`smoke` isolates the failing hop. If the VM step fails:
`python3 tools/stack.py logs telegraf` — usually MQTT auth (regenerate with
`init` after deleting server/.env + server/mosquitto/passwd) or a topic that
doesn't match `frodas/+/<component>/+/state` (see server/telegraf/telegraf.conf).
For live traffic inspection prefer the mock-device skill plus
`mosquitto_sub -h localhost -u <user> -P <pass> -t 'frodas/#' -v`.
