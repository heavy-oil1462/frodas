---
name: server-stack
description: Bring up, check, or manage the frodas server stack (mosquitto, telegraf, VictoriaMetrics, Grafana via docker compose). Use when the user wants the server running, MQTT credentials created, smoke tests, logs, or is debugging why telemetry isn't reaching Grafana.
---

# frodas server stack

Everything goes through `tools/stack.py` (plumbing: esphome_skills.stack);
do not run raw `docker compose` or craft ad-hoc mosquitto commands.
Services: mosquitto, telegraf, victoriametrics, grafana.

```bash
python3 tools/stack.py init    # once: server/.env + mosquitto passwd
python3 tools/stack.py up
python3 tools/stack.py smoke   # MQTT round trip -> VM metric -> Grafana health
python3 tools/stack.py logs [mosquitto|telegraf|victoriametrics|grafana]
python3 tools/stack.py down
```

Endpoints after `up`: Grafana http://localhost:3000 (creds in server/.env,
dashboard auto-provisioned in folder "Frodas"), VictoriaMetrics
http://localhost:8428, MQTT localhost:1883 (authenticated).

Debugging the telemetry path: `smoke` isolates the failing hop. If the VM
step fails: `python3 tools/stack.py logs telegraf`; usually MQTT auth
(regenerate with `init` after deleting server/.env +
server/mosquitto/passwd) or a topic that does not match
`frodas/+/<component>/+/state` (server/telegraf/telegraf.conf; the
object_id becomes the metric `<object_id>_value`).

Canonical doc:
https://github.com/heavy-oil1462/esphome-skills/blob/main/skills/server-stack.md
