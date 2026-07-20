# frodas — agent guide

Autonomous solar-powered greenhouse controller. ESP32/ESPHome firmware +
MQTT/VictoriaMetrics/Grafana server + Home Assistant integration.

## Non-negotiable design principles

1. **Local autonomy**: every control loop (watering, ventilation, load
   shedding) runs on the ESP32. Nothing plant- or safety-critical may depend
   on WiFi/MQTT/HA. No deep sleep — the CPU always runs; only the radio duty-cycles.
2. **Power budget**: WiFi off by default, short radio window every N minutes.
3. **Generic**: users adopt via ESPHome packages + substitutions, never by
   editing core logic. `docs/EXTENDING.md` and `docs/PROTOCOL.md` are the
   public contract — keep firmware, mock device, telegraf config, dashboards
   and docs in sync when semantics change.

## How to work here

- Use the skills: `verify` (before every commit), `protocol-test`,
  `server-stack`, `mock-device`, `firmware`, `simulator` (real firmware
  under QEMU — use for rule-level testing; slow).
- Prefer the reusable tools in `tools/` over ad-hoc shell; extend a tool if
  something is missing, then document it in the matching skill.
- Everything must stay green: `nix develop -c python3 tools/validate.py` and
  `nix develop -c python3 tools/test_protocol.py`. CI runs both.
- Full firmware build (`esphome compile`) after touching any lambda —
  `esphome config` does not catch C++ errors.
- Entity naming: object_ids derive from entity names; they are load-bearing
  across telegraf metrics (`<object_id>_value`), the Grafana dashboard, the
  HA package, and the mock. Rename only with a sweep across all of them.
