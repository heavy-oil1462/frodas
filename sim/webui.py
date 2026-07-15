#!/usr/bin/env python3
"""frodas simulator web UI — drive the REAL firmware's inputs by hand.

Serves a small control panel (stdlib http.server, no dependencies beyond
paho-mqtt) that lets you set every simulated greenhouse input individually
and watch the firmware react:

  * Sensor injections — published retained to <root>/<node>/sim/<key>, where
    the mqtt_subscribe twins in esphome/packages/sim-sensors.yaml pick them
    up under the real sensor ids. The watering / ventilation / load-shedding
    lambdas that respond are the exact C++ flashed onto real nodes.
  * Time of day — served to the firmware over SNTP. greenhouse-base.yaml
    points ${sntp_server} at this process (10.0.2.2 from inside QEMU), so the
    firmware's clock — and therefore the watering window logic — follows the
    slider. Requires --ntp-port 123 (privileged; fine inside the container).
  * Live entity state — everything the node publishes, straight from the
    broker, so you can see valve/pump/vent/tier respond within seconds.

Runs inside the sim container (see sim/Containerfile) but works equally well
straight from a checkout against any broker:

    python3 sim/webui.py --broker 192.168.1.10 --username frodas \
        --password ... --http-port 8080 --ntp-port 1123

MQTT credentials fall back to env: MQTT_HOST, MQTT_PORT, MQTT_USER,
MQTT_PASSWORD, SIM_NODE, MQTT_ROOT.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools._lib import mqtt_client  # noqa: E402

NTP_EPOCH_DELTA = 2208988800  # 1900-01-01 -> 1970-01-01

# key -> (label, unit, min, max, step, default)  — keys are the sim/<key>
# topics of esphome/packages/sim-sensors.yaml. Keep the two in sync.
INJECTIONS = {
    "temperature": ("Greenhouse Temperature", "°C", -15.0, 55.0, 0.5, 21.0),
    "humidity": ("Greenhouse Humidity", "%", 0.0, 100.0, 1.0, 65.0),
    "soil": ("Soil Moisture", "%", 0.0, 100.0, 1.0, 50.0),
    "battery": ("Battery Voltage", "V", 10.0, 14.6, 0.05, 13.2),
    "illuminance": ("Greenhouse Illuminance", "lx", 0.0, 100000.0, 500.0, 20000.0),
    "solar_current": ("Solar Current", "A", 0.0, 3.2, 0.05, 1.0),
}

PRESETS = {
    "Sunny noon": {"time": "12:30", "temperature": 32, "humidity": 55,
                   "soil": 45, "battery": 13.4, "illuminance": 65000,
                   "solar_current": 2.4},
    "Dry morning (watering window)": {"time": "06:30", "temperature": 18,
                                      "humidity": 70, "soil": 20,
                                      "battery": 13.1, "illuminance": 8000,
                                      "solar_current": 0.8},
    "Hot & humid (vent opens)": {"time": "14:00", "temperature": 33,
                                 "humidity": 88, "soil": 55,
                                 "battery": 13.3, "illuminance": 70000,
                                 "solar_current": 2.0},
    "Cold night": {"time": "03:00", "temperature": 1, "humidity": 90,
                   "soil": 50, "battery": 12.9, "illuminance": 0,
                   "solar_current": 0},
    "Low battery (tier 2)": {"battery": 12.3, "solar_current": 0.1},
    "Critical battery (tier 3)": {"battery": 11.8, "solar_current": 0},
}


class SimClock:
    """Simulated wall clock: real time plus a user-controlled offset."""

    def __init__(self, tz: ZoneInfo):
        self.tz = tz
        self.offset = 0.0
        self.mode = "real"
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return time.time() + self.offset

    def set_time_of_day(self, hour: int, minute: int) -> None:
        """Pin the clock so local time (in tz) reads hour:minute today."""
        local_now = datetime.now(self.tz)
        target = local_now.replace(hour=hour, minute=minute, second=0,
                                   microsecond=0)
        with self._lock:
            self.offset = target.timestamp() - time.time()
            self.mode = "sim"

    def set_real(self) -> None:
        with self._lock:
            self.offset = 0.0
            self.mode = "real"

    def snapshot(self) -> dict:
        local = datetime.fromtimestamp(self.now(), self.tz)
        with self._lock:
            return {
                "mode": self.mode,
                "offset_s": round(self.offset, 1),
                "local": local.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "hour": local.hour,
                "minute": local.minute,
            }


class NtpServer(threading.Thread):
    """Minimal SNTP server answering with the simulated clock."""

    def __init__(self, clock: SimClock, port: int):
        super().__init__(daemon=True, name="ntp")
        self.clock = clock
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.requests = 0

    @staticmethod
    def _ts(unix: float) -> bytes:
        secs = int(unix) + NTP_EPOCH_DELTA
        frac = int((unix % 1) * (1 << 32))
        return struct.pack("!II", secs & 0xFFFFFFFF, frac & 0xFFFFFFFF)

    def run(self) -> None:
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                return
            if len(data) < 48:
                continue
            now = self.clock.now()
            vn = (data[0] >> 3) & 0x07
            reply = bytearray(48)
            reply[0] = (0 << 6) | (vn << 3) | 4  # LI=0, version echoed, mode=server
            reply[1] = 2                         # stratum 2
            reply[2] = data[2]                   # poll (echoed)
            reply[3] = 0xEC                      # precision ~1 us
            reply[12:16] = b"SIM "               # reference id
            reply[16:24] = self._ts(now)         # reference timestamp
            reply[24:32] = data[40:48]           # originate = client transmit
            reply[32:40] = self._ts(now)         # receive timestamp
            reply[40:48] = self._ts(now)         # transmit timestamp
            self.sock.sendto(bytes(reply), addr)
            self.requests += 1


class SimBridge:
    """MQTT side: publish injections, mirror everything the node says."""

    def __init__(self, args):
        self.root = args.root
        self.node = args.node
        self.prefix = f"{args.root}/{args.node}"
        self.injected: dict[str, float] = {}
        self.states: dict[str, tuple[str, float]] = {}
        self.connected = False
        self._lock = threading.Lock()
        self.client = mqtt_client(f"frodas-sim-ui-{os.getpid()}",
                                  args.username, args.password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.radio_always_on = not args.no_radio_always_on
        self.client.connect(args.broker, args.port, keepalive=30)
        self.client.loop_start()

    # paho 1.x/2.x callback signatures differ — soak up the extras.
    def _on_connect(self, client, userdata, *rest):
        self.connected = True
        client.subscribe(f"{self.prefix}/#")
        if self.radio_always_on:
            # Retained ON keeps the sim reachable between emulated radio
            # windows; flip the switch off in HA/UI to watch real duty cycle.
            client.publish(f"{self.prefix}/switch/radio_always_on/command",
                           "ON", retain=True)

    def _on_disconnect(self, client, userdata, *rest):
        self.connected = False

    def _on_message(self, client, userdata, msg):
        rel = msg.topic[len(self.prefix) + 1:]
        payload = msg.payload.decode("utf-8", "replace")
        with self._lock:
            if rel.startswith("sim/"):
                if payload:
                    try:
                        self.injected[rel[4:]] = float(payload)
                    except ValueError:
                        pass
                else:
                    self.injected.pop(rel[4:], None)
            else:
                self.states[rel] = (payload, time.time())

    def inject(self, key: str, value: float) -> None:
        self.client.publish(f"{self.prefix}/sim/{key}",
                            f"{value:.3f}".rstrip("0").rstrip("."),
                            retain=True)

    def clear_injections(self) -> None:
        for key in INJECTIONS:
            self.client.publish(f"{self.prefix}/sim/{key}", "", retain=True)

    def set_radio_always_on(self, on: bool) -> None:
        self.client.publish(f"{self.prefix}/switch/radio_always_on/command",
                            "ON" if on else "OFF", retain=True)

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            entities = {}
            for rel, (payload, ts) in sorted(self.states.items()):
                parts = rel.split("/")
                if len(parts) == 3 and parts[2] == "state":
                    entities.setdefault(parts[0], {})[parts[1]] = {
                        "value": payload, "age_s": round(now - ts)}
            status = self.states.get("status", ("unknown", 0))
            return {
                "broker_connected": self.connected,
                "node_status": status[0],
                "injected": dict(self.injected),
                "entities": entities,
            }


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>frodas simulator</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#111a14;color:#dce8dd}
 header{padding:.8rem 1.2rem;background:#18251b;border-bottom:1px solid #2c4231}
 h1{font-size:1.1rem;margin:0}h1 small{color:#7fa387;font-weight:normal}
 main{display:grid;grid-template-columns:minmax(320px,430px) 1fr;gap:1rem;padding:1rem}
 @media(max-width:860px){main{grid-template-columns:1fr}}
 section{background:#18251b;border:1px solid #2c4231;border-radius:8px;padding:1rem}
 h2{font-size:.85rem;text-transform:uppercase;letter-spacing:.08em;color:#7fa387;margin:0 0 .8rem}
 .row{margin-bottom:.9rem}
 .row label{display:flex;justify-content:space-between;font-size:.85rem;margin-bottom:.2rem}
 .row output{color:#9fd4a8;font-variant-numeric:tabular-nums}
 input[type=range]{width:100%;accent-color:#4caf6d}
 button{background:#24402b;color:#dce8dd;border:1px solid #3c5f44;border-radius:6px;
        padding:.35rem .7rem;margin:.15rem;cursor:pointer;font-size:.8rem}
 button:hover{background:#2f5238}
 table{width:100%;border-collapse:collapse;font-size:.82rem}
 td,th{padding:.25rem .4rem;border-bottom:1px solid #26382b;text-align:left}
 td.v{color:#9fd4a8;font-variant-numeric:tabular-nums}
 td.age{color:#5d7a63;font-size:.75rem;white-space:nowrap}
 .pill{display:inline-block;padding:.1rem .55rem;border-radius:999px;font-size:.75rem}
 .on{background:#1d4d2a;color:#9fd4a8}.off{background:#4d1d1d;color:#d4a09f}
 .muted{color:#5d7a63;font-size:.78rem}
 #timeview{font-size:1.3rem;color:#9fd4a8;font-variant-numeric:tabular-nums}
</style></head><body>
<header><h1>frodas simulator <small id="conn">connecting…</small></h1></header>
<main>
<div>
<section><h2>Sensor injections</h2><div id="sliders"></div>
 <button onclick="clearInj()">Clear all injections</button>
 <p class="muted">Values are published retained to
 <code>__PREFIX__/sim/&lt;key&gt;</code>; the firmware's mqtt_subscribe
 sensors adopt them under the real sensor ids.</p></section>
<section><h2>Time of day</h2>
 <div id="timeview">--:--</div><div class="muted" id="timemode"></div>
 <div class="row"><label>Set local time <output id="tod_out"></output></label>
 <input type="range" id="tod" min="0" max="1439" step="5"
  oninput="todPreview()" onchange="setTime()"></div>
 <button onclick="realTime()">Follow real time</button>
 <p class="muted">Served to the firmware over SNTP — watering windows use
 this clock.</p></section>
<section><h2>Presets</h2><div id="presets"></div></section>
<section><h2>Radio</h2>
 <button onclick="radio(true)">Radio always on</button>
 <button onclick="radio(false)">Duty-cycled radio</button>
 <p class="muted">Duty-cycled: the node disconnects between radio windows —
 exactly like hardware. Injections still land (retained).</p></section>
</div>
<div>
<section><h2>Node state <span id="status" class="pill off">?</span></h2>
 <div id="entities" class="muted">waiting for data…</div></section>
</div>
</main>
<script>
const INJ = __INJECTIONS__;
const PRESETS = __PRESETS__;
function el(id){return document.getElementById(id)}
function fmt(m){return String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0')}
function todPreview(){el('tod_out').textContent = fmt(+el('tod').value)}
function build(){
  let h='';
  for(const [k,[label,unit,min,max,step,dflt]] of Object.entries(INJ)){
    h+=`<div class="row"><label>${label}
      <output id="o_${k}">– ${unit}</output></label>
      <input type="range" id="s_${k}" min="${min}" max="${max}" step="${step}"
       value="${dflt}" oninput="preview('${k}')" onchange="inject('${k}')"></div>`;
  }
  el('sliders').innerHTML=h;
  let p='';
  for(const name of Object.keys(PRESETS))
    p+=`<button onclick='preset(${JSON.stringify(name)})'>${name}</button>`;
  el('presets').innerHTML=p;
}
function preview(k){
  const u=INJ[k][1];
  el('o_'+k).textContent=`${(+el('s_'+k).value).toLocaleString()} ${u} (pending)`;
}
async function post(url,body){await fetch(url,{method:'POST',body:JSON.stringify(body)})}
function inject(k){post('/api/inject',{key:k,value:+el('s_'+k).value})}
function clearInj(){post('/api/clear',{})}
function setTime(){const m=+el('tod').value;post('/api/time',{mode:'sim',hour:Math.floor(m/60),minute:m%60})}
function realTime(){post('/api/time',{mode:'real'})}
function radio(on){post('/api/radio',{always_on:on})}
function preset(name){
  const p=PRESETS[name];
  for(const [k,v] of Object.entries(p)){
    if(k==='time'){const[h,m]=v.split(':');post('/api/time',{mode:'sim',hour:+h,minute:+m});}
    else{el('s_'+k).value=v;preview(k);inject(k);}
  }
}
async function refresh(){
  try{
    const r=await (await fetch('/api/state')).json();
    el('conn').textContent=r.mqtt.broker_connected?'broker connected':'broker DISCONNECTED';
    const st=r.mqtt.node_status;
    const pill=el('status');pill.textContent=st;
    pill.className='pill '+(st==='online'?'on':'off');
    el('timeview').textContent=r.clock.local;
    el('timemode').textContent=r.clock.mode==='sim'
      ?'simulated (offset '+r.clock.offset_s+' s)':'real time';
    for(const [k,v] of Object.entries(r.mqtt.injected))
      if(INJ[k]) el('o_'+k).textContent=`${(+v).toLocaleString()} ${INJ[k][1]}`;
    let h='';
    for(const [comp,ents] of Object.entries(r.mqtt.entities)){
      h+=`<table><tr><th colspan=3>${comp}</th></tr>`;
      for(const [oid,s] of Object.entries(ents))
        h+=`<tr><td>${oid}</td><td class="v">${s.value}</td><td class="age">${s.age_s}s ago</td></tr>`;
      h+='</table>';
    }
    el('entities').innerHTML=h||'<span class="muted">no state yet — node booting?</span>';
  }catch(e){el('conn').textContent='UI backend unreachable';}
}
build();todPreview();refresh();setInterval(refresh,2000);
</script></body></html>
"""


def make_handler(bridge: SimBridge, clock: SimClock, ntp: NtpServer):
    page = (PAGE
            .replace("__INJECTIONS__", json.dumps(INJECTIONS))
            .replace("__PRESETS__", json.dumps(PRESETS))
            .replace("__PREFIX__", bridge.prefix))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep container logs readable
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if self.path == "/":
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json({
                    "mqtt": bridge.snapshot(),
                    "clock": clock.snapshot(),
                    "ntp_requests": ntp.requests,
                })
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            if self.path == "/api/inject":
                key = body.get("key")
                if key not in INJECTIONS:
                    return self._json({"error": f"unknown key {key!r}"}, 400)
                lo, hi = INJECTIONS[key][2], INJECTIONS[key][3]
                try:
                    value = min(hi, max(lo, float(body.get("value"))))
                except (TypeError, ValueError):
                    return self._json({"error": "value must be a number"}, 400)
                bridge.inject(key, value)
            elif self.path == "/api/clear":
                bridge.clear_injections()
            elif self.path == "/api/time":
                if body.get("mode") == "real":
                    clock.set_real()
                else:
                    try:
                        clock.set_time_of_day(int(body["hour"]) % 24,
                                              int(body["minute"]) % 60)
                    except (KeyError, ValueError):
                        return self._json({"error": "need hour/minute"}, 400)
            elif self.path == "/api/radio":
                bridge.set_radio_always_on(bool(body.get("always_on")))
            else:
                return self._json({"error": "not found"}, 404)
            self._json({"ok": True})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--broker", default=os.environ.get("MQTT_HOST"),
                    help="MQTT broker host (env MQTT_HOST)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("MQTT_PORT", "1883")))
    ap.add_argument("--username", default=os.environ.get("MQTT_USER"))
    ap.add_argument("--password", default=os.environ.get("MQTT_PASSWORD"))
    ap.add_argument("--root", default=os.environ.get("MQTT_ROOT", "frodas"))
    ap.add_argument("--node", default=os.environ.get("SIM_NODE", "frodas-sim"))
    ap.add_argument("--timezone",
                    default=os.environ.get("SIM_TIMEZONE", "Europe/Stockholm"),
                    help="must match the firmware's `timezone` substitution")
    ap.add_argument("--http-port", type=int,
                    default=int(os.environ.get("SIM_HTTP_PORT", "8080")))
    ap.add_argument("--ntp-port", type=int,
                    default=int(os.environ.get("SIM_NTP_PORT", "123")))
    ap.add_argument("--no-radio-always-on", action="store_true",
                    help="do not publish the retained Radio Always On=ON "
                         "command at startup (node then duty-cycles MQTT)")
    args = ap.parse_args()
    if not args.broker:
        ap.error("--broker (or MQTT_HOST) is required")

    clock = SimClock(ZoneInfo(args.timezone))
    ntp = NtpServer(clock, args.ntp_port)
    ntp.start()
    bridge = SimBridge(args)
    httpd = ThreadingHTTPServer(("0.0.0.0", args.http_port),
                                make_handler(bridge, clock, ntp))
    print(f"frodas simulator UI: http://0.0.0.0:{args.http_port} "
          f"(node {bridge.prefix}, NTP on udp/{args.ntp_port}, "
          f"broker {args.broker}:{args.port})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
