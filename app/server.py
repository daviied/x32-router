"""X32 Router web server. Stdlib only."""
import hashlib
import hmac
import json
import mimetypes
import os
import socket
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import x32map as M
from x32 import X32, discover
from relay import Relay

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
CFG.setdefault("relay", {"enabled": False, "name": "X32-Relay"})
RELAY = Relay(X, port=int(os.environ.get("RELAY_PORT", "10023")), name=CFG["relay"].get("name") or "X32-Relay")
if CFG["relay"].get("enabled"):
    RELAY.start()

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
PORTS_IN = M.IN_PORTS
PORTS_OUT = M.OUT_PORTS


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
    kind = "insert" if d.get("kind") == "insert" else "box"
    ins_type = d.get("insType") if d.get("insType") in M.INS_TYPES else "AUX"
    ins_max = M.INS_TYPES[ins_type][1]
    ins_start = 1 if ins_type == "POOL" else i(d.get("insStart"), 1, ins_max, 1)
    ins_count = i(d.get("insCount"), 0, ins_max - ins_start + 1, 0)
    if kind == "insert":
        in_count = out_count = 0
    else:
        ins_count = 0
    return {
        "id": str(d.get("id") or uuid.uuid4().hex[:8])[:16],
        "name": str(d.get("name") or "Device")[:40],
        "preset": str(d.get("preset") or "CUSTOM")[:10],
        "kind": kind,
        "inPort": in_port, "inStart": in_start, "inCount": in_count,
        "outPort": out_port, "outStart": out_start, "outCount": out_count,
        "insType": ins_type, "insStart": ins_start, "insCount": ins_count,
        "cols": i(d.get("cols"), 1, 16, 8),
        "labels": clean_labels(d.get("labels"), {"in": in_count, "out": out_count, "ins": ins_count}),
        "io": clean_io(d.get("io"), ins_count) if kind == "insert" and ins_type in ("AUX", "POOL") else {},
    }


def clean_io(io, cnt):
    """Outboard wiring per slot: {"1": {"send": "devId:jack", "ret": "devId:jack"}}."""
    out = {}
    io = io if isinstance(io, dict) else {}
    for s, v in io.items():
        try:
            s = int(s)
        except (TypeError, ValueError):
            continue
        if not (1 <= s <= cnt) or not isinstance(v, dict):
            continue
        e = {}
        for k in ("send", "ret"):
            x = str(v.get(k) or "")[:40]
            if x.count(":") == 1:
                e[k] = x
        if e:
            out[str(s)] = e
    return out


def split_ref(ref):
    try:
        dev, j = ref.split(":")
        return dev, int(j)
    except (AttributeError, ValueError):
        return None, 0


def clean_labels(lab, counts):
    out = {}
    lab = lab if isinstance(lab, dict) else {}
    for k, cnt in counts.items():
        src = lab.get(k) if isinstance(lab.get(k), dict) else {}
        m = {}
        for j, v in src.items():
            try:
                j = int(j)
            except (TypeError, ValueError):
                continue
            v = str(v).strip()[:32]
            if 1 <= j <= cnt and v:
                m[str(j)] = v
        out[k] = m
    return out


def dev_defaults(d):
    """Fill fields missing in configs saved by older versions."""
    return {**M.PRESETS["CUSTOM"], "inCount": 0, "outCount": 0, "outPort": "",
            "labels": {"in": {}, "out": {}, "ins": {}}, "io": {}, **d}


def allowed_block_paths():
    p = {M.p_in_block(b) for b in M.IN_BLOCKS}
    p.add(M.p_in_block("AUX"))
    for port in M.BLOCK_OUT_PORTS:
        p |= {M.p_out_block(port, b) for b in M.out_blocks(port)}
    p |= {M.p_xlr_block(b) for b in M.XLR_BLOCKS}
    return p


ALLOWED_BLOCKS = allowed_block_paths()


class DualStackServer(ThreadingHTTPServer):
    """Listen on IPv6 and IPv4. With IPv4 only, names that resolve to IPv6 first
    (localhost, raspberrypi.local, an nginx upstream) waited ~2 s on every request."""
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def make_server(port):
    try:
        return DualStackServer(("::", port), H)
    except OSError:  # no IPv6 on this host
        srv = ThreadingHTTPServer(("0.0.0.0", port), H)
        srv.daemon_threads = True
        return srv


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
        devices = [dev_defaults(d) for d in CFG.get("devices", [])]
        ip = CFG.get("x32Ip", "")
    jacks_in = {d["id"]: {} for d in devices}
    slots = {d["id"]: {} for d in devices}
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
                dv = next(x for x in devices if x["id"] == did)
                item["plug"] = dv["labels"].get("in", {}).get(str(j), "")
        elif r["kind"] == "USB":
            item["src"] = "USB " + ("L" if r["n"] == 1 else "R")
        else:
            item["src"] = "?"
        item["routable"] = r["kind"] == "UIN"
        # insert
        iv = st.get(M.p_ins_sel(f))
        ion = st.get(M.p_ins_on(f))
        item["ins"] = M.ins_label(iv)
        item["insOn"] = bool(ion)
        t, n = M.ins_from_value(iv)
        unit = pool_assign().get(str(n)) if t == "AUX" else None
        pd, pch = split_ref(unit) if unit else (None, 0)
        pdev = next((d for d in devices if d["id"] == pd and d["insType"] == "POOL"), None)
        if pdev and 1 <= pch <= pdev["insCount"]:
            item["ins"] = f"{pdev['name']} {pch} (Aux {n})" if pdev["insCount"] > 1 else f"{pdev['name']} (Aux {n})"
            if ion:
                slots[pdev["id"]].setdefault(pch, []).append(f)
        elif t:
            for d in devices:
                if d["kind"] == "insert" and d["insType"] == t and d["insStart"] <= n < d["insStart"] + d["insCount"]:
                    s = n - d["insStart"] + 1
                    item["ins"] = f"{d['name']} {s}"
                    slots[d["id"]].setdefault(s, []).append(f)
        faders.append(item)
    jacks_out = {}
    for d in devices:
        o = {}
        if d["outPort"]:
            for j in range(1, d["outCount"] + 1):
                r = M.resolve_dev_output(d["outPort"], d["outStart"] + j - 1, st)
                pick = "mix" if r["kind"] == "MIX" else ("uout" if r["kind"] == "UOUT" else "")
                o[j] = {"label": r["label"], "routable": bool(pick), "pick": pick, "src": r.get("src"),
                        "via": f"User Out {r['n']}" if r["kind"] == "UOUT" else ""}
        jacks_out[d["id"]] = o
    blocks = {
        "in": [{"path": M.p_in_block(b), "label": f"Ch {b}", "value": st.get(M.p_in_block(b))} for b in M.IN_BLOCKS],
        "aux": {"path": M.p_in_block("AUX"), "label": "Aux 1-6", "value": st.get(M.p_in_block("AUX"))},
        "out": {port: [{"path": M.p_out_block(port, b), "label": f"{M.PORT_NAMES[port]} Out {b}",
                        "value": st.get(M.p_out_block(port, b))} for b in M.out_blocks(port)] for port in M.BLOCK_OUT_PORTS},
        "xlr": [{"path": M.p_xlr_block(b), "label": f"XLR Out {b}", "value": st.get(M.p_xlr_block(b))} for b in M.XLR_BLOCKS],
    }
    slot_info = {}
    for d in devices:
        if d["kind"] != "insert":
            continue
        info = {}
        for s in range(1, d["insCount"] + 1):
            n = d["insStart"] + s - 1
            e = {}
            if d["insType"] == "FX":
                rack = (n + 1) // 2
                fi = M.fx_type_info(st.get(M.p_fx_type(rack)))
                e = {"fx": f"FX{rack} {fi['type']}", "warn": "" if fi["insertOk"] is not False
                     else f"FX{rack} has {fi['type']} loaded. That is a send-type effect, not an insert type."}
            else:
                io = d["io"].get(str(s), {})
                e = {"send": ref_label(devices, io.get("send"), "out"), "ret": ref_label(devices, io.get("ret"), "in")}
                if d["insType"] == "POOL":
                    unit = f"{d['id']}:{s}"
                    e["aux"] = next((int(k) for k, u in pool_assign().items() if u == unit and slots[d["id"]].get(s)), 0)
                    if not (e["send"] and e["ret"]):
                        e["warn"] = "Set the send and return jacks on the Devices tab."
            info[s] = e
        slot_info[d["id"]] = info
    return {
        "connected": X.connected, "info": X.info, "mock": MOCK, "x32Ip": ip,
        "devices": devices, "faders": faders, "jacksIn": jacks_in, "jacksOut": jacks_out,
        "slots": slots, "slotInfo": slot_info, "blocks": blocks,
        "auxPool": aux_pool(), "auxUse": aux_use_view(st, devices),
    }


def aux_pool():
    return [n for n in CFG.get("auxPool", list(range(1, M.AUX_COUNT + 1))) if 1 <= n <= M.AUX_COUNT]


def pool_assign():
    return CFG.setdefault("auxAssign", {})


def aux_in_use(st):
    """Aux N -> fader using it as an insert (insert on)."""
    use = {}
    for f in M.FADERS:
        t, n = M.ins_from_value(st.get(M.p_ins_sel(f)))
        if t == "AUX" and st.get(M.p_ins_on(f)):
            use.setdefault(n, f)
    return use


def uin_users(st, n):
    return [M.fader_label(f) for f in M.FADERS if f.startswith("ch")
            and (r := M.resolve_fader(f, st))["kind"] == "UIN" and r["n"] == n]


def aux_use_view(st, devices):
    use = aux_in_use(st)
    out = []
    for n in range(1, M.AUX_COUNT + 1):
        unit = pool_assign().get(str(n))
        dev_id, ch = split_ref(unit) if unit else (None, 0)
        d = next((x for x in devices if x["id"] == dev_id), None)
        out.append({"aux": n, "pool": n in aux_pool(), "fader": M.fader_label(use[n]) if n in use else "",
                    "unit": (f"{d['name']} {ch}" if d and d["insCount"] > 1 else d["name"]) if d and n in use else "",
                    "uinBusy": ", ".join(uin_users(st, n))})
    return out


def ref_label(devices, ref, side):
    dev_id, j = split_ref(ref)
    d = next((x for x in devices if x["id"] == dev_id), None) or get_dev(dev_id)
    if not d:
        return ""
    lab = d["labels"].get(side, {}).get(str(j), "")
    return f"{d['name']} {'In' if side == 'in' else 'Out'} {j}" + (f" ({lab})" if lab else "")


# The console's own rear jacks. Always there for insert wiring, no device needed.
CONSOLE_DEVS = [
    {"id": "@xlr", "name": "Console XLR", "kind": "box", "inPort": "LOCAL", "inStart": 1, "inCount": 32,
     "outPort": "XLR", "outStart": 1, "outCount": 16},
    {"id": "@aux", "name": "Console Aux", "kind": "box", "inPort": "AUX", "inStart": 1, "inCount": 6,
     "outPort": "AUXOUT", "outStart": 1, "outCount": 6},
]


STATIC_META = {
    "inOpts": M.in_block_options(), "auxOpts": M.aux_block_options(), "outOpts": M.out_block_options(), "xlrOpts": M.xlr_block_options(), "consoleDevs": CONSOLE_DEVS,
    "uoutSources": M.uout_source_groups(), "mixSources": M.mix_source_groups(),
    "presets": M.PRESETS, "portNames": M.PORT_NAMES, "portSize": M.PORT_SIZE,
    "inPorts": M.IN_PORTS, "outPorts": M.OUT_PORTS,
    "insTypes": {k: {"name": M.INS_TYPE_NAMES[k], "count": v[1]} for k, v in M.INS_TYPES.items()},
}


def get_dev(dev_id):
    d = next((x for x in CFG["devices"] if x["id"] == dev_id), None)
    d = d or next((x for x in CONSOLE_DEVS if x["id"] == dev_id), None)
    return dev_defaults(d) if d else None


# ---------- routing actions ----------
def route_input(fader, dev_id, jack):
    """jack 0 or dev_id empty = unassign (User In set to Off)."""
    if fader not in M.FADERS:
        return 400, {"error": "Bad fader"}
    r = M.resolve_fader(fader, X.snapshot())
    if r["kind"] != "UIN":
        return 409, {"error": f"{M.fader_label(fader)} is not fed by a User In. Set its block to User In on the Blocks tab."}
    if not dev_id:
        X.set(M.p_uin(r["n"]), 0)
        return 200, {"ok": True, "msg": f"{M.fader_label(fader)} unassigned (User In {r['n']} = Off)"}
    d = get_dev(dev_id)
    if not d or not (1 <= jack <= d["inCount"]):
        return 400, {"error": "Bad device input"}
    n = d["inStart"] + jack - 1
    v = M.src_value(d["inPort"], n)
    X.set(M.p_uin(r["n"]), v)
    return 200, {"ok": True, "msg": f"{d['name']} In {jack} ({M.PORT_NAMES[d['inPort']]} {n}) → User In {r['n']} → {M.fader_label(fader)}"}


def route_output(dev_id, jack, source):
    """source 0 = unassign (Off)."""
    d = get_dev(dev_id)
    if not d or not d["outPort"] or not (1 <= jack <= d["outCount"]):
        return 400, {"error": "Bad device output"}
    m = d["outStart"] + jack - 1
    port = d["outPort"]
    r = M.resolve_dev_output(port, m, X.snapshot())
    if r["kind"] == "MIX":
        if not (0 <= source <= M.MIX_MAX):
            return 400, {"error": "Bad source"}
        X.set(M.p_local_out(port, r["n"]), source)
        return 200, {"ok": True, "msg": f"{M.mix_label(source)} → {d['name']} Out {jack} ({M.PORT_NAMES[port]} {m})"}
    if not (0 <= source <= M.UOUT_MAX):
        return 400, {"error": "Bad source"}
    if r["kind"] != "UOUT":
        return 409, {"error": f"{M.PORT_NAMES[port]} {m} is not fed by a User Out. Set its block to User Out on the Blocks tab."}
    X.set(M.p_uout(r["n"]), source)
    return 200, {"ok": True, "msg": f"{M.src_label(source)} → User Out {r['n']} → {d['name']} Out {jack}"}


pool_lock = threading.Lock()


def free_pool_aux(n, st):
    """Forget the unit on Aux n and switch its send jack off."""
    unit = pool_assign().pop(str(n), None)
    if not unit:
        return
    dev_id, ch = split_ref(unit)
    d = get_dev(dev_id)
    if not d:
        return
    sdev, sj = split_ref(d["io"].get(str(ch), {}).get("send"))
    od = get_dev(sdev) if sdev else None
    if od and od["outPort"] != "AUXOUT" and 1 <= sj <= od["outCount"]:
        r = M.resolve_dev_output(od["outPort"], od["outStart"] + sj - 1, st)
        if r["kind"] == "UOUT" and r.get("src") == 200 + n:
            X.set(M.p_uout(r["n"]), 0)


def route_insert(fader, dev_id, slot):
    """Empty dev_id = remove insert."""
    if fader not in M.FADERS:
        return 400, {"error": "Bad fader"}
    if not dev_id:
        with pool_lock:
            st = X.snapshot()
            t, n = M.ins_from_value(st.get(M.p_ins_sel(fader)))
            X.set(M.p_ins_on(fader), 0)
            X.set(M.p_ins_sel(fader), 0)
            note = ""
            if t == "AUX" and str(n) in pool_assign():
                free_pool_aux(n, st)
                with cfg_lock:
                    save_cfg(CFG)
                note = f" · Aux {n} is free again"
        return 200, {"ok": True, "msg": f"{M.fader_label(fader)} insert removed{note}"}
    d = get_dev(dev_id)
    if not d or d["kind"] != "insert" or not (1 <= slot <= d["insCount"]):
        return 400, {"error": "Bad insert slot"}
    if d["insType"] == "POOL":
        with pool_lock:
            return route_pool_insert(fader, d, slot)
    v = M.ins_value(d["insType"], d["insStart"] + slot - 1)
    X.set(M.p_ins_sel(fader), v)
    X.set(M.p_ins_on(fader), 1)
    msg = f"{d['name']} {slot} ({M.ins_label(v)}) inserted on {M.fader_label(fader)}"
    notes = []
    if d["insType"] == "AUX":
        notes = route_outboard_io(d, slot)
    else:
        rack = (d["insStart"] + slot) // 2
        fi = M.fx_type_info(X.snapshot().get(M.p_fx_type(rack)))
        if fi["insertOk"] is False:
            notes.append(f"Warning: FX{rack} has {fi['type']} loaded (send-type effect). Load an insert-type effect.")
    return 200, {"ok": True, "msg": " · ".join([msg] + notes), "warn": any(n.startswith("Warning") for n in notes)}


def route_pool_insert(fader, d, ch):
    """Insert an outboard unit: pick a free Aux, route its send and return, set the insert."""
    st = X.snapshot()
    unit = f"{d['id']}:{ch}"
    uname = f"{d['name']} {ch}" if d["insCount"] > 1 else d["name"]
    io = d["io"].get(str(ch), {})
    sdev, sj = split_ref(io.get("send"))
    rdev, rj = split_ref(io.get("ret"))
    od = get_dev(sdev) if sdev else None
    idev = get_dev(rdev) if rdev else None
    if not (od and od["outPort"] and 1 <= sj <= od["outCount"]):
        return 409, {"error": f"{uname}: set its send jack on the Devices tab."}
    pinned = 0  # local Aux Out k carries the Aux k insert send itself: the unit must use Aux k
    if od["outPort"] == "AUXOUT":
        pinned = od["outStart"] + sj - 1
    elif M.resolve_dev_output(od["outPort"], od["outStart"] + sj - 1, st)["kind"] != "UOUT":
        hint = (f"set XLR Out block {M.XLR_BLOCKS[(od['outStart'] + sj - 2) // 4]} to User Out"
                if od["outPort"] == "XLR" else "set its block to User Out")
        return 409, {"error": f"{uname}: the send jack {od['name']} Out {sj} is not fed by a User Out. On the Blocks tab, {hint}."}
    if not (idev and 1 <= rj <= idev["inCount"]):
        return 409, {"error": f"{uname}: set its return jack on the Devices tab."}

    assign, use, pool = pool_assign(), aux_in_use(st), aux_pool()
    for k, u in assign.items():
        if u == unit and int(k) in use and use[int(k)] != fader:
            return 409, {"error": f"{uname} is already inserted on {M.fader_label(use[int(k)])}. Remove it there first."}
    t, cur = M.ins_from_value(st.get(M.p_ins_sel(fader)))
    cur = cur if t == "AUX" and st.get(M.p_ins_on(fader)) else 0
    prev = next((int(k) for k, u in assign.items() if u == unit), 0)
    order = [n for n in [cur, prev] + pool if n]
    reasons, n = [], 0
    if pinned:
        order = [pinned]
    for cand in dict.fromkeys(order):
        if cand not in pool:
            if pinned:
                reasons.append(f"its send is local Aux Out {cand}, but Aux {cand} is not in the Aux pool")
            continue
        if cand in use and use[cand] != fader:
            reasons.append(f"Aux {cand} on {M.fader_label(use[cand])}")
            continue
        busy = uin_users(st, cand)
        if busy:
            reasons.append(f"Aux {cand} return needs User In {cand} (used by {', '.join(busy)})")
            continue
        n = cand
        break
    if not n:
        return 409, {"error": "No free Aux for an insert. " + ("; ".join(dict.fromkeys(reasons)) or "The Aux pool is empty (Devices tab).")}

    notes = []
    if cur and cur != n and str(cur) in assign:
        free_pool_aux(cur, st)
    if prev and prev != n:
        assign.pop(str(prev), None)
    if assign.get(str(n)) not in (None, unit):
        free_pool_aux(n, st)
    want = M.AUX_BLOCK_OPTS.index(("UIN", 6))
    av = st.get(M.p_in_block("AUX"))
    kind, c = M.AUX_BLOCK_OPTS[av] if av is not None and 0 <= av < len(M.AUX_BLOCK_OPTS) else ("?", 0)
    if not (kind == "UIN" and n <= c):
        X.set(M.p_in_block("AUX"), want)
        notes.append("Aux In block set to User In 1-6")
    if not pinned:
        code, res = route_output(od["id"], sj, 200 + n)
        if code != 200:
            return code, res
    X.set(M.p_uin(n), M.src_value(idev["inPort"], idev["inStart"] + rj - 1))
    X.set(M.p_ins_sel(fader), M.ins_value("AUX", n))
    X.set(M.p_ins_on(fader), 1)
    assign[str(n)] = unit
    with cfg_lock:
        save_cfg(CFG)
    msg = (f"{uname} inserted on {M.fader_label(fader)} using Aux {n} "
           f"(send → {od['name']} Out {sj}, return ← {idev['name']} In {rj})")
    return 200, {"ok": True, "msg": " · ".join([msg] + notes), "aux": n}


def route_outboard_io(d, slot):
    """Route Aux Out N (send) and Aux In N (return) to the stage box jacks set for this slot."""
    n = d["insStart"] + slot - 1  # Aux number
    io = d["io"].get(str(slot), {})
    # Not set = the unit is on the console's rear Aux Out N / Aux In N jacks
    io = {"send": io.get("send") or f"@aux:{n}", "ret": io.get("ret") or f"@aux:{n}"}
    notes = []
    st = X.snapshot()
    # send: Aux Out N -> stage box output (needs a User Out output)
    sdev, sj = split_ref(io.get("send"))
    if sdev:
        od = get_dev(sdev)
        if od and od["outPort"] in M.BLOCK_OUT_PORTS + ("XLR",) and 1 <= sj <= od["outCount"]:
            code, res = route_output(sdev, sj, 200 + n)
            notes.append(f"send → {od['name']} Out {sj}" if code == 200 else "Warning: " + res["error"])
        elif od and od["outPort"] == "AUXOUT" and od["outStart"] + sj - 1 == n:
            notes.append(f"send on rear Aux Out {n}")
        else:
            notes.append(f"Warning: send jack must be an AES50/Card/XLR output or rear Aux Out {n}.")
    # return: stage box input -> Aux In N
    rdev, rj = split_ref(io.get("ret"))
    if rdev:
        idev = get_dev(rdev)
        if not idev or not (1 <= rj <= idev["inCount"]):
            notes.append("Warning: return jack not found.")
        else:
            port, pn = idev["inPort"], idev["inStart"] + rj - 1
            av = st.get(M.p_in_block("AUX"))
            kind, c = M.AUX_BLOCK_OPTS[av] if av is not None and 0 <= av < len(M.AUX_BLOCK_OPTS) else ("?", 0)
            if port == "AUX" and pn == n and (kind == "AUX" or n > c):
                notes.append(f"return on Aux In {n}")
            elif kind == "UIN" and n <= c:
                users = [M.fader_label(f) for f in M.FADERS if f.startswith("ch")
                         and (r := M.resolve_fader(f, st))["kind"] == "UIN" and r["n"] == n]
                if users:
                    notes.append(f"Warning: return not routed. Aux {n} returns through User In {n} "
                                 f"(Aux In block is on User In), but User In {n} feeds {', '.join(users)}. "
                                 f"Move those channels to other User Ins.")
                else:
                    X.set(M.p_uin(n), M.src_value(port, pn))
                    notes.append(f"return ← {idev['name']} In {rj} via User In {n}")
            elif kind == port and n <= c and pn == n:
                notes.append(f"return ← {idev['name']} In {rj}")
            else:
                notes.append(f"Warning: return not routed. Set the Aux In block to User In 1-{max(2, n + n % 2)} on the Blocks tab.")
    return notes


def route_fxinsert(fader):
    """Put the first free insert (unit or slot) on the fader."""
    if fader not in M.FADERS:
        return 400, {"error": "Bad fader"}
    st = X.snapshot()
    if st.get(M.p_ins_sel(fader)) and st.get(M.p_ins_on(fader)):
        return 409, {"error": f"{M.fader_label(fader)} already has an insert ({M.ins_label(st.get(M.p_ins_sel(fader)))}). Remove it first."}
    used = {st.get(M.p_ins_sel(f)) for f in M.FADERS if st.get(M.p_ins_on(f))}
    use = aux_in_use(st)
    busy_units = {u for k, u in pool_assign().items() if int(k) in use}
    last_err = None
    for d in (dev_defaults(x) for x in CFG["devices"]):
        if d["kind"] != "insert":
            continue
        for s in range(1, d["insCount"] + 1):
            if d["insType"] == "POOL":
                if f"{d['id']}:{s}" in busy_units:
                    continue
                code, res = route_insert(fader, d["id"], s)
                if code == 200:
                    return code, res
                last_err = (code, res)
            elif M.ins_value(d["insType"], d["insStart"] + s - 1) not in used:
                return route_insert(fader, d["id"], s)
    return last_err or (409, {"error": "No free insert. Add an insert device or remove an insert."})


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

    def _reply(self, code, res):
        """Action reply. On success it carries the new state, so the page needs no second request."""
        if code == 200:
            res = {**res, "state": build_state()}
        return self._json(code, res)

    def _events(self):
        """Server-Sent Events: push the full state each time it changes."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")  # nginx: do not buffer
        self.end_headers()
        last_v, last_conn, last_ping = -1, None, 0.0
        try:
            while True:
                with X.changed:
                    X.changed.wait_for(lambda: X.version != last_v, timeout=2)
                now = time.time()
                if X.version != last_v or X.connected != last_conn:
                    time.sleep(0.03)  # merge bursts (e.g. a full re-read)
                    last_v, last_conn = X.version, X.connected
                    self.wfile.write(b"data: " + json.dumps(build_state()).encode() + b"\n\n")
                    self.wfile.flush()
                    last_ping = now
                elif now - last_ping > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = now
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

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
            if path == "/api/events":
                return self._events()
            if path == "/api/relay":
                return self._json(200, RELAY.status())
            if path == "/api/discover":
                found = discover()
                if MOCK and not found:
                    found = [{"ip": "127.0.0.1", "name": "Mock console", "model": "X32", "fw": "4.06"}]
                return self._json(200, {"consoles": found})
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
                    if "auxPool" in body and isinstance(body["auxPool"], list):
                        CFG["auxPool"] = sorted({int(n) for n in body["auxPool"] if str(n).isdigit() and 1 <= int(n) <= M.AUX_COUNT})
                    if "x32Ip" in body:
                        ip = str(body["x32Ip"]).strip()[:64]
                        CFG["x32Ip"] = ip
                        X.set_ip(ip)
                    save_cfg(CFG)
                X.bump()
                return self._reply(200, {"ok": True})
            if path == "/api/route/input":
                code, res = route_input(str(body.get("fader")), str(body.get("device") or ""),int(body.get("jack", 0)))
                return self._reply(code, res)
            if path == "/api/route/output":
                code, res = route_output(str(body.get("device")), int(body.get("jack", 0)), int(body.get("source", -1)))
                return self._reply(code, res)
            if path == "/api/route/fxinsert":
                code, res = route_fxinsert(str(body.get("fader")))
                return self._reply(code, res)
            if path == "/api/route/insert":
                code, res = route_insert(str(body.get("fader")), str(body.get("device") or ""), int(body.get("slot", 0)))
                return self._reply(code, res)
            if path == "/api/block":
                p, v = str(body.get("path")), int(body.get("value", -1))
                if p not in ALLOWED_BLOCKS:
                    return self._json(400, {"error": "Bad block"})
                limit = len(M.AUX_BLOCK_OPTS) if p.endswith("/AUX") else (
                    len(M.IN_BLOCK_OPTS) if "/IN/" in p else
                    len(M.XLR_BLOCK_OPTS) if "/OUT/" in p else len(M.OUT_BLOCK_OPTS))
                if not (0 <= v < limit):
                    return self._json(400, {"error": "Bad value"})
                old = X.snapshot().get(p)
                X.set(p, v)
                note = ""
                if "/OUT/" in p and old is not None and 0 <= old < len(M.XLR_BLOCK_OPTS):
                    # XLR block Output x -> User Out y: keep the jacks' signal in empty User Outs
                    (ok_, os_), (nk, ns) = M.XLR_BLOCK_OPTS[old], M.XLR_BLOCK_OPTS[v]
                    if ok_ == "OUT" and nk == "UOUT":
                        st, kept = X.snapshot(), 0
                        for i in range(4):
                            if not st.get(M.p_uout(ns + i)):
                                X.set(M.p_uout(ns + i), 168 + os_ + i)
                                kept += 1
                        note = f"{kept} jack(s) keep Output {os_}-{os_ + 3} through User Out {ns}-{ns + 3}"
                return self._reply(200, {"ok": True, "msg": note})
            if path == "/api/relay":
                with cfg_lock:
                    rc = CFG.setdefault("relay", {"enabled": False, "name": "X32-Relay"})
                    if "name" in body:
                        rc["name"] = (str(body["name"]).strip() or "X32-Relay")[:24]
                        RELAY.name = rc["name"]
                    if "enabled" in body:
                        rc["enabled"] = bool(body["enabled"])
                    save_cfg(CFG)
                if rc["enabled"] and not RELAY.running:
                    RELAY.start()
                elif not rc["enabled"] and RELAY.running:
                    RELAY.stop()
                st = RELAY.status()
                if rc["enabled"] and not st["running"]:
                    return self._json(500, {**st, "error": st["error"] or "Relay did not start"})
                return self._json(200, st)
            if path == "/api/refresh":
                X.refresh()
                return self._reply(200, {"ok": True})
        except (ValueError, TypeError):
            return self._json(400, {"error": "Bad request"})
        return self._json(404, {"error": "Not found"})


if __name__ == "__main__":
    print(f"X32 Router on :{PORT}  X32={CFG.get('x32Ip') or '(not set)'}  mock={MOCK}", flush=True)
    make_server(PORT).serve_forever()
