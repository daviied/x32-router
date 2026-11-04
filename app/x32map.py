"""X32 routing tables (firmware 4.x) and routing logic.

All OSC enum values live here. If a value is wrong for your firmware,
fix it in this file only. Verify against X32-Edit if routing looks off.
"""

PORT_NAMES = {"LOCAL": "Local In", "A": "AES50-A", "B": "AES50-B", "CARD": "Card", "AUX": "Aux In",
              "XLR": "Local Out", "AUXOUT": "Aux Out"}
PORT_SHORT = {"LOCAL": "L", "A": "A", "B": "B", "CARD": "C", "AUX": "Aux", "XLR": "Out", "AUXOUT": "AuxOut"}
PORT_SIZE = {"LOCAL": 32, "A": 48, "B": 48, "CARD": 32, "AUX": 6, "XLR": 16, "AUXOUT": 6}
IN_PORTS = ("LOCAL", "A", "B", "CARD", "AUX")
OUT_PORTS = ("A", "B", "CARD", "XLR", "AUXOUT")
BLOCK_OUT_PORTS = ("A", "B", "CARD")      # routed by 8-blocks + User Out
LOCAL_OUT_PORTS = ("XLR", "AUXOUT")       # routed per output by mix source

# ---- User In source values: /config/userrout/in/01..32 ----
# 0 OFF, 1-32 Local, 33-80 AES50A, 81-128 AES50B, 129-160 Card, 161-166 Aux, 167-168 TB
_SRC_BASE = {"LOCAL": 0, "A": 32, "B": 80, "CARD": 128, "AUX": 160}


def src_value(port, n):
    return _SRC_BASE[port] + n


def src_from_value(v):
    """Return (port, n) or a special tuple for non-physical sources."""
    if v <= 0:
        return ("OFF", 0)
    for port in IN_PORTS:
        base = _SRC_BASE[port]
        if base < v <= base + PORT_SIZE[port]:
            return (port, v - base)
    if v == 167:
        return ("TB", 1)
    if v == 168:
        return ("TB", 2)
    return ("UOUT", v)  # output-type sources, only valid for user out


def src_label(v):
    if v is None:
        return "?"
    p, n = src_from_value(v)
    if p == "OFF":
        return "Off"
    if p == "TB":
        return "Talkback " + ("Int" if n == 1 else "Ext")
    if p == "UOUT":
        return uout_extra_label(v)
    return f"{PORT_NAMES[p]} {n}"


# ---- User Out extra sources: /config/userrout/out/01..48 ----
# 169-184 Out 1-16, 185-200 P16 1-16, 201-206 Aux Out 1-6, 207-208 Monitor L/R
UOUT_MAX = 208


def uout_extra_label(v):
    if 169 <= v <= 184:
        return f"Output {v - 168}"
    if 185 <= v <= 200:
        return f"P16 {v - 184}"
    if 201 <= v <= 206:
        return f"Aux Out {v - 200}"
    if v == 207:
        return "Monitor L"
    if v == 208:
        return "Monitor R"
    return f"Value {v}"


def uout_source_groups():
    """Source list for AES50/Card outputs (User Out)."""
    g = [
        {"name": "Outputs (main patch 1-16)", "items": [[168 + i, f"Output {i}"] for i in range(1, 17)]},
        {"name": "P16 Ultranet", "items": [[184 + i, f"P16 {i}"] for i in range(1, 17)]},
        {"name": "Aux Out / Monitor", "items": [[200 + i, f"Aux Out {i}"] for i in range(1, 7)] + [[207, "Monitor L"], [208, "Monitor R"]]},
    ]
    for port in IN_PORTS:
        g.append({"name": f"{PORT_NAMES[port]} (direct)",
                  "items": [[src_value(port, i), f"{PORT_NAMES[port]} {i}"] for i in range(1, PORT_SIZE[port] + 1)]})
    g.append({"name": "Other", "items": [[167, "Talkback Int"], [168, "Talkback Ext"]]})
    return g


# ---- Local output source: /outputs/main/01..16/src, /outputs/aux/01..06/src ----
# 0 Off, 1 Main L, 2 Main R, 3 M/C, 4-19 Bus 1-16, 20-25 Matrix 1-6,
# 26-57 Direct Ch 1-32, 58-65 Direct Aux 1-8, 66-73 Direct FX 1-8, 74-75 Monitor L/R, 76 Talkback
MIX_MAX = 76


def mix_label(v):
    if v is None:
        return "?"
    if v == 0:
        return "Off"
    if v <= 3:
        return ["Main L", "Main R", "Mono/C"][v - 1]
    if v <= 19:
        return f"Bus {v - 3}"
    if v <= 25:
        return f"Matrix {v - 19}"
    if v <= 57:
        return f"Ch {v - 25} Direct"
    if v <= 65:
        return f"Aux {v - 57} Direct"
    if v <= 73:
        return f"FX {v - 65} Direct"
    return {74: "Monitor L", 75: "Monitor R", 76: "Talkback"}.get(v, f"Value {v}")


def mix_source_groups():
    r = lambda a, b: [[v, mix_label(v)] for v in range(a, b + 1)]
    return [
        {"name": "Main / Bus / Matrix", "items": r(1, 25)},
        {"name": "Channel direct outs", "items": r(26, 57)},
        {"name": "Aux / FX direct outs", "items": r(58, 73)},
        {"name": "Monitor / Talkback", "items": r(74, 76)},
    ]


def p_local_out(port, n):
    return f"/outputs/main/{n:02d}/src" if port == "XLR" else f"/outputs/aux/{n:02d}/src"


# ---- Inserts: /ch/NN/insert/sel + /on (same under /auxin/NN) ----
# sel: 0 Off, 1-8 FX1L FX1R .. FX4L FX4R, 9-14 Aux 1-6
INS_TYPES = {"AUX": (9, 6), "FX": (1, 8)}  # base value, slot count
INS_TYPE_NAMES = {"AUX": "Aux send/return", "FX": "Internal FX"}


def ins_value(typ, slot):
    return INS_TYPES[typ][0] + slot - 1


def ins_from_value(v):
    if v and 1 <= v <= 8:
        return ("FX", v)
    if v and 9 <= v <= 14:
        return ("AUX", v - 8)
    return (None, 0)


def ins_label(v):
    t, n = ins_from_value(v)
    if t == "FX":
        return f"FX{(n + 1) // 2}{'L' if n % 2 else 'R'}"
    if t == "AUX":
        return f"Aux {n}"
    return ""


# ---- FX rack types: /fx/1..4/type ----
# Values 0-26 are reverb / delay / modulation types (normally send FX).
# Values 27+ are channel-processor types that work as inserts.
FX_TYPES = ["HALL", "AMBI", "RPLT", "ROOM", "CHAM", "PLAT", "VREV", "VRM", "GATE", "RVRS", "DLY", "3TAP",
            "4TAP", "CRS", "FLNG", "PHAS", "DIMC", "FILT", "ROTA", "PAN", "SUB", "D/RV", "CR/R", "FL/R",
            "D/CR", "D/FL", "MODD", "GEQ2", "GEQ", "TEQ2", "TEQ", "DES2", "DES", "P1A", "P1A2", "PQ5",
            "PQ5S", "WAVD", "LIM", "CMB", "CMB2", "FAC", "FAC1M", "FAC2", "LEC", "LEC2", "ULC", "ULC2",
            "ENH2", "ENH", "EXC2", "EXC", "IMG", "EDI", "SON", "AMP2", "AMP", "DRV2", "DRV", "PIT2", "PIT"]
FX_FIRST_INSERT = 27


def p_fx_type(rack):
    return f"/fx/{rack}/type"


def fx_type_info(v):
    if v is None:
        return {"type": "?", "insertOk": None}
    name = FX_TYPES[v] if 0 <= v < len(FX_TYPES) else f"Type {v}"
    return {"type": name, "insertOk": v >= FX_FIRST_INSERT}


def _fader_base(f):
    return f"/ch/{int(f[2:]):02d}" if f.startswith("ch") else f"/auxin/{int(f[3:]):02d}"


def p_ins_sel(f):
    return _fader_base(f) + "/insert/sel"


def p_ins_on(f):
    return _fader_base(f) + "/insert/on"


# ---- Input blocks: /config/routing/IN/1-8 .. 25-32 ----
IN_BLOCK_OPTS = []
for i in range(4):
    IN_BLOCK_OPTS.append(("LOCAL", i * 8 + 1))
for i in range(6):
    IN_BLOCK_OPTS.append(("A", i * 8 + 1))
for i in range(6):
    IN_BLOCK_OPTS.append(("B", i * 8 + 1))
for i in range(4):
    IN_BLOCK_OPTS.append(("CARD", i * 8 + 1))
for i in range(4):
    IN_BLOCK_OPTS.append(("UIN", i * 8 + 1))

IN_BLOCKS = ["1-8", "9-16", "17-24", "25-32"]


def kind_name(k):
    return "User In" if k == "UIN" else ("User Out" if k == "UOUT" else PORT_NAMES.get(k, k))


def in_block_options():
    return [[i, f"{kind_name(k)} {s}-{s + 7}"] for i, (k, s) in enumerate(IN_BLOCK_OPTS)]


# ---- Aux In block: /config/routing/IN/AUX ----
# value -> (kind, count): aux 1..count come from kind 1..count, rest from Aux In.
AUX_BLOCK_OPTS = [("AUX", 0)]
for k in ("LOCAL", "A", "B", "CARD", "UIN"):
    for c in (2, 4, 6):
        AUX_BLOCK_OPTS.append((k, c))


def aux_block_options():
    out = []
    for i, (k, c) in enumerate(AUX_BLOCK_OPTS):
        out.append([i, "Aux In 1-6" if k == "AUX" else f"{kind_name(k)} 1-{c}"])
    return out


# ---- Output blocks: /config/routing/AES50A|AES50B|CARD/<block> ----
OUT_BLOCK_OPTS = []
for i in range(4):
    OUT_BLOCK_OPTS.append(("LOCAL", i * 8 + 1))
for i in range(6):
    OUT_BLOCK_OPTS.append(("A", i * 8 + 1))
for i in range(6):
    OUT_BLOCK_OPTS.append(("B", i * 8 + 1))
for i in range(4):
    OUT_BLOCK_OPTS.append(("CARD", i * 8 + 1))
OUT_BLOCK_OPTS += [("OUT", 1), ("OUT", 9), ("P16", 1), ("P16", 9), ("AUXMON", 1)]
for i in range(6):
    OUT_BLOCK_OPTS.append(("UOUT", i * 8 + 1))

OUT_PORT_PATH = {"A": "AES50A", "B": "AES50B", "CARD": "CARD"}


def out_blocks(port):
    return [f"{i * 8 + 1}-{i * 8 + 8}" for i in range(PORT_SIZE[port] // 8)]


def out_block_label(k, s):
    if k == "OUT":
        return f"Output {s}-{s + 7}"
    if k == "P16":
        return f"P16 {s}-{s + 7}"
    if k == "AUXMON":
        return "Aux Out 1-6 / Mon"
    return f"{kind_name(k)} {s}-{s + 7}"


def out_block_options():
    return [[i, out_block_label(k, s)] for i, (k, s) in enumerate(OUT_BLOCK_OPTS)]


def out_block_slot_label(k, n):
    if k == "OUT":
        return f"Output {n}"
    if k == "P16":
        return f"P16 {n}"
    if k == "AUXMON":
        return f"Aux Out {n}" if n <= 6 else ("Monitor L" if n == 7 else "Monitor R")
    if k == "UOUT":
        return f"User Out {n}"
    return f"{PORT_NAMES[k]} {n}"


# ---- Paths ----
def p_in_block(b):
    return f"/config/routing/IN/{b}"


def p_out_block(port, b):
    return f"/config/routing/{OUT_PORT_PATH[port]}/{b}"


def p_uin(n):
    return f"/config/userrout/in/{n:02d}"


def p_uout(n):
    return f"/config/userrout/out/{n:02d}"


def p_ch_name(n):
    return f"/ch/{n:02d}/config/name"


def p_aux_name(n):
    return f"/auxin/{n:02d}/config/name"


# ---- Faders ----
FADERS = [f"ch{i}" for i in range(1, 33)] + [f"aux{i}" for i in range(1, 9)]


def fader_label(f):
    return f"Ch {f[2:]}" if f.startswith("ch") else f"Aux {f[3:]}"


def all_read_paths():
    paths = [p_in_block(b) for b in IN_BLOCKS] + [p_in_block("AUX")]
    for port in BLOCK_OUT_PORTS:
        paths += [p_out_block(port, b) for b in out_blocks(port)]
    paths += [p_uin(i) for i in range(1, 33)]
    paths += [p_uout(i) for i in range(1, 49)]
    paths += [p_ch_name(i) for i in range(1, 33)]
    paths += [p_aux_name(i) for i in range(1, 9)]
    for port in LOCAL_OUT_PORTS:
        paths += [p_local_out(port, i) for i in range(1, PORT_SIZE[port] + 1)]
    for f in FADERS:
        paths += [p_ins_sel(f), p_ins_on(f)]
    paths += [p_fx_type(r) for r in range(1, 5)]
    return paths


def resolve_fader(f, st):
    """What feeds a fader. Returns dict: kind (port/UIN/USB/AUX), n, phys."""
    if f.startswith("ch"):
        k = int(f[2:])
        b = IN_BLOCKS[(k - 1) // 8]
        v = st.get(p_in_block(b))
        if v is None or not (0 <= v < len(IN_BLOCK_OPTS)):
            return {"kind": "?", "n": 0}
        kind, s = IN_BLOCK_OPTS[v]
        n = s + (k - 1) % 8
    else:
        k = int(f[3:])
        if k >= 7:
            return {"kind": "USB", "n": k - 6}
        v = st.get(p_in_block("AUX"))
        if v is None or not (0 <= v < len(AUX_BLOCK_OPTS)):
            return {"kind": "?", "n": 0}
        kind, c = AUX_BLOCK_OPTS[v]
        if kind == "AUX" or k > c:
            kind, n = "AUX", k
        else:
            n = k  # assumption: Aux k <- source k of the chosen block
    if kind == "UIN":
        uv = st.get(p_uin(n))
        phys = src_from_value(uv) if uv is not None else ("?", 0)
        return {"kind": "UIN", "n": n, "phys": list(phys)}
    return {"kind": kind, "n": n, "phys": [kind, n]}


def resolve_dev_output(port, m, st):
    """What feeds physical output m on a port."""
    if port in LOCAL_OUT_PORTS:
        v = st.get(p_local_out(port, m))
        return {"kind": "MIX", "n": m, "src": v, "label": mix_label(v)}
    b = out_blocks(port)[(m - 1) // 8]
    v = st.get(p_out_block(port, b))
    if v is None or not (0 <= v < len(OUT_BLOCK_OPTS)):
        return {"kind": "?", "label": "?"}
    kind, s = OUT_BLOCK_OPTS[v]
    n = s + (m - 1) % 8
    if kind == "UOUT":
        uv = st.get(p_uout(n))
        return {"kind": "UOUT", "n": n, "src": uv, "label": src_label(uv)}
    return {"kind": kind, "n": n, "label": out_block_slot_label(kind, n)}


# ---- Device presets ----
_BASE = {"kind": "box", "inPort": "A", "inStart": 1, "inCount": 0, "outPort": "", "outStart": 1, "outCount": 0,
         "insType": "AUX", "insStart": 1, "insCount": 0, "cols": 8}
PRESETS = {
    "S32": {**_BASE, "name": "S32", "inCount": 32, "outPort": "A", "outCount": 16},
    "S16": {**_BASE, "name": "S16", "inCount": 16, "outPort": "A", "outCount": 8},
    "CARD": {**_BASE, "name": "Card", "inPort": "CARD", "inCount": 32, "outPort": "CARD", "outCount": 32},
    "LOCAL": {**_BASE, "name": "X32 Local", "inPort": "LOCAL", "inCount": 32, "outPort": "XLR", "outCount": 16},
    "AUXIO": {**_BASE, "name": "X32 Aux I/O", "inPort": "AUX", "inCount": 6, "outPort": "AUXOUT", "outCount": 6, "cols": 6},
    "INS_AUX": {**_BASE, "kind": "insert", "name": "Outboard insert", "insType": "AUX", "insCount": 2},
    "INS_FX": {**_BASE, "kind": "insert", "name": "Internal FX insert", "insType": "FX", "insCount": 8},
    "CUSTOM": {**_BASE, "name": "Custom", "inCount": 8, "outPort": "A", "outCount": 8},
}
