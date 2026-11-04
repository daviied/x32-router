"""X32 Router web server. Stdlib only."""
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import x32map as M
from x32 import X32

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
DATA = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA / "config.json"
PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET = (os.environ.get("SESSION_SECRET") or secrets.token_hex(32)).encode()
PORT = int(os.environ.get("PORT", "8080"))
MOCK = os.environ.get("X32_MOCK") == "1"
SESSION_DAYS = 30

if not PASSWORD:
    raise SystemExit("APP_PASSWORD is not set. Refusing to start without a password.")

cfg_lock = threading.Lock()


def load_cfg():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        s32 = dict(M.PRESETS["S32"], id="s32", preset="S32", inStart=17)
        return {"x32Ip": os.environ.get("X32_IP", ""), "devices": [s32]}


def save_cfg(c):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=2))
    tmp.replace(CONFIG_FILE)


CFG = load_cfg()
X = X32(CFG.get("x32Ip", ""), mock=MOCK)
X.refresh()

# ---------- auth ----------
def make_token():
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def check_token(tok):
    try:
        exp, sig = tok.split(".")
        good = hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:
        return False


login_hits = {}


def rate_ok(ip):
    now = time.time()
    hits = [t for t in login_hits.get(ip, []) if now - t < 60]
    login_hits[ip] = hits
    if len(hits) >= 8:
        return False
    hits.append(now)
    return True


# ---------- validation ----------
PORTS_IN = ("LOCAL", "A", "B", "CARD", "AUX")
PORTS_OUT = ("A", "B", "CARD")


def clean_device(d):
    def i(v, lo, hi, dflt):
        try:
            v = int(v)
        except Exception:
            return dflt
        return max(lo, min(hi, v))
    in_port = d.get("inPort") if d.get("inPort") in PORTS_IN else "A"
    out_port = d.get("outPort") if d.get("outPort") in PORTS_OUT else ""
    in_start = i(d.get("inStart"), 1, M.PORT_SIZE[in_port], 1)
    in_count = i(d.get("inCount"), 0, M.PORT_SIZE[in_port] - in_start + 1, 0)
    out_start = i(d.get("outStart"), 1, M.PORT_SIZE[out_port] if out_port else 1, 1)
    out_count = i(d.get("outCount"), 0, (M.PORT_SIZE[out_port] - out_start + 1) if out_port else 0, 0)
    return {
        "id": str(d.get("id") or uuid.uuid4().hex[:8])[:16],
        "name": str(d.get("name") or "Device")[:40],
        "preset": str(d.get("preset") or "CUSTOM")[:10],
        "inPort": in_port, "inStart": in_start, "inCount": in_count,
        "outPort": out_port, "outStart": out_start, "outCount": out_count,
        "cols": i(d.get("cols"), 1, 16, 8),
    }


def allowed_block_paths():
    p = {M.p_in_block(b) for b in M.IN_BLOCKS}
    p.add(M.p_in_block("AUX"))
    for port in PORTS_OUT:
        p |= {M.p_out_block(port, b) for b in M.out_blocks(port)}
    return p


ALLOWED_BLOCKS = allowed_block_paths()


# ---------- state view ----------
def find_jack(devices, port, n):
    for d in devices:
        if d["inPort"] == port and d["inStart"] <= n < d["inStart"] + d["inCount"]:
            return d, n - d["inStart"] + 1
    return None, None


def phys_label(devices, port, n):
    if port in M.PORT_NAMES:
        d, j = find_jack(devices, port, n)
        base = f"{M.PORT_SHORT[port]}{n}"
        return (f"{d['name']} In {j}", base, d["id"], j) if d else (f"{M.PORT_NAMES[port]} {n}", base, None, None)
    if port == "OFF":
        return ("Off", "", None, None)
    if port == "TB":
        return ("Talkback", "", None, None)
    return ("?", "", None, None)


def build_state():
    st = X.snapshot()
    with cfg_lock:
        devices = list(CFG.get("devices", []))
        ip = CFG.get("x32Ip", "")
    jacks_in = {d["id"]: {} for d in devices}
    faders = []
    for f in M.FADERS:
        r = M.resolve_fader(f, st)
        name = st.get(M.p_ch_name(int(f[2:]))) if f.startswith("ch") else st.get(M.p_aux_name(int(f[3:])))
        item = {"id": f, "label": M.fader_label(f), "name": name or "", "kind": r["kind"], "n": r["n"]}
        if r["kind"] == "UIN":
            item["via"] = f"User In {r['n']}"
        if "phys" in r:
            lab, short, did, j = phys_label(devices, *r["phys"])
            item["src"], item["srcShort"] = lab, short
            if did:
                jacks_in[did].setdefault(j, []).append(f)
        elif r["kind"] == "USB":
            item["src"] = "USB " + ("L" if r["n"] == 1 else "R")
        else:
            item["src"] = "?"
        item["routable"] = r["kind"] == "UIN"
        faders.append(item)
    jacks_out = {}
    for d in devices:
        o = {}
        if d["outPort"]:
            for j in range(1, d["outCount"] + 1):
                r = M.resolve_dev_output(d["outPort"], d["outStart"] + j - 1, st)
                o[j] = {"label": r["label"], "routable": r["kind"] == "UOUT",
                        "via": f"User Out {r['n']}" if r["kind"] == "UOUT" else ""}
        jacks_out[d["id"]] = o
    blocks = {
        "in": [{"path": M.p_in_block(b), "label": f"Ch {b}", "value": st.get(M.p_in_block(b))} for b in M.IN_BLOCKS],
        "aux": {"path": M.p_in_block("AUX"), "label": "Aux 1-6", "value": st.get(M.p_in_block("AUX"))},
        "out": {port: [{"path": M.p_out_block(port, b), "label": f"{M.PORT_NAMES[port]} Out {b}",
                        "value": st.get(M.p_out_block(port, b))} for b in M.out_blocks(port)] for port in PORTS_OUT},
    }
    return {
        "connected": X.connected, "info": X.info, "mock": MOCK, "x32Ip": ip,
        "devices": devices, "faders": faders, "jacksIn": jacks_in, "jacksOut": jacks_out,
        "blocks": blocks,
    }


STATIC_META = {
    "inOpts": M.in_block_options(), "auxOpts": M.aux_block_options(), "outOpts": M.out_block_options(),
    "uoutSources": M.uout_source_groups(), "presets": M.PRESETS, "portNames": M.PORT_NAMES,
    "portSize": M.PORT_SIZE,
}


# ---------- routing actions ----------
def route_input(fader, dev_id, jack):
    if fader not in M.FADERS:
        return 400, {"error": "Bad fader"}
    d = next((x for x in CFG["devices"] if x["id"] == dev_id), None)
    if not d or not (1 <= jack <= d["inCount"]):
        return 400, {"error": "Bad device input"}
    r = M.resolve_fader(fader, X.snapshot())
    if r["kind"] != "UIN":
        return 409, {"error": f"{M.fader_label(fader)} is not fed by a User In. Set its block to User In on the Blocks tab."}
    n = d["inStart"] + jack - 1
    v = M.src_value(d["inPort"], n)
    X.set(M.p_uin(r["n"]), v)
    return 200, {"ok": True, "msg": f"{d['name']} In {jack} ({M.PORT_NAMES[d['inPort']]} {n}) → User In {r['n']} → {M.fader_label(fader)}"}


def route_output(dev_id, jack, source):
    d = next((x for x in CFG["devices"] if x["id"] == dev_id), None)
    if not d or not d["outPort"] or not (1 <= jack <= d["outCount"]):
        return 400, {"error": "Bad device output"}
    if not (0 <= source <= 208):
        return 400, {"error": "Bad source"}
    m = d["outStart"] + jack - 1
    r = M.resolve_dev_output(d["outPort"], m, X.snapshot())
    if r["kind"] != "UOUT":
        return 409, {"error": f"{M.PORT_NAMES[d['outPort']]} Out {m} is not fed by a User Out. Set its block to User Out on the Blocks tab."}
    X.set(M.p_uout(r["n"]), source)
    return 200, {"ok": True, "msg": f"{M.src_label(source)} → User Out {r['n']} → {d['name']} Out {jack}"}


# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    server_version = "X32Router"

    def log_message(self, *a):
        pass

    def _json(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _authed(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "x32r" and check_token(v):
                return True
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200_000:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _same_origin(self):
        # CSRF guard for state-changing requests
        return self.headers.get("X-Requested-With") == "x32r"

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            if path == "/api/auth":
                return self._json(200, {"authed": self._authed()})
            if not self._authed():
                return self._json(401, {"error": "Login required"})
            if path == "/api/state":
                return self._json(200, build_state())
            if path == "/api/meta":
                return self._json(200, STATIC_META)
            return self._json(404, {"error": "Not found"})
        self._static(path)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        f = (PUBLIC / path.lstrip("/")).resolve()
        if not str(f).startswith(str(PUBLIC.resolve())) or not f.is_file():
            f = PUBLIC / "index.html"
        b = f.read_bytes()
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if f.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._same_origin():
            return self._json(403, {"error": "Bad request"})
        body = self._body()
        if path == "/api/login":
            if not rate_ok(self._client_ip()):
                return self._json(429, {"error": "Too many tries. Wait one minute."})
            if not hmac.compare_digest(str(body.get("password", "")).encode(), PASSWORD.encode()):
                return self._json(401, {"error": "Wrong password"})
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            ck = f"x32r={make_token()}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_DAYS * 86400}{secure}"
            return self._json(200, {"ok": True}, {"Set-Cookie": ck})
        if path == "/api/logout":
            return self._json(200, {"ok": True}, {"Set-Cookie": "x32r=; Path=/; Max-Age=0"})
        if not self._authed():
            return self._json(401, {"error": "Login required"})
        try:
            if path == "/api/config":
                with cfg_lock:
                    if "devices" in body and isinstance(body["devices"], list):
                        CFG["devices"] = [clean_device(d) for d in body["devices"][:32] if isinstance(d, dict)]
                    if "x32Ip" in body:
                        ip = str(body["x32Ip"]).strip()[:64]
                        CFG["x32Ip"] = ip
                        X.set_ip(ip)
                    save_cfg(CFG)
                return self._json(200, {"ok": True})
            if path == "/api/route/input":
                code, res = route_input(str(body.get("fader")), str(body.get("device")), int(body.get("jack", 0)))
                return self._json(code, res)
            if path == "/api/route/output":
                code, res = route_output(str(body.get("device")), int(body.get("jack", 0)), int(body.get("source", -1)))
                return self._json(code, res)
            if path == "/api/block":
                p, v = str(body.get("path")), int(body.get("value", -1))
                if p not in ALLOWED_BLOCKS:
                    return self._json(400, {"error": "Bad block"})
                limit = len(M.AUX_BLOCK_OPTS) if p.endswith("/AUX") else (
                    len(M.IN_BLOCK_OPTS) if "/IN/" in p else len(M.OUT_BLOCK_OPTS))
                if not (0 <= v < limit):
                    return self._json(400, {"error": "Bad value"})
                X.set(p, v)
                return self._json(200, {"ok": True})
            if path == "/api/refresh":
                X.refresh()
                return self._json(200, {"ok": True})
        except (ValueError, TypeError):
            return self._json(400, {"error": "Bad request"})
        return self._json(404, {"error": "Not found"})


if __name__ == "__main__":
    print(f"X32 Router on :{PORT}  X32={CFG.get('x32Ip') or '(not set)'}  mock={MOCK}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
