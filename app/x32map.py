"""X32 routing tables (firmware 4.x) and routing logic.

All OSC enum values live here. If a value is wrong for your firmware,
fix it in this file only. Verify against X32-Edit if routing looks off.
"""

PORT_NAMES = {"LOCAL": "Local", "A": "AES50-A", "B": "AES50-B", "CARD": "Card", "AUX": "Aux In"}
PORT_SHORT = {"LOCAL": "L", "A": "A", "B": "B", "CARD": "C", "AUX": "Aux"}
PORT_SIZE = {"LOCAL": 32, "A": 48, "B": 48, "CARD": 32, "AUX": 6}

# ---- User In source values: /config/userrout/in/01..32 ----
# 0 OFF, 1-32 Local, 33-80 AES50A, 81-128 AES50B, 129-160 Card, 161-166 Aux, 167-168 TB
_SRC_BASE = {"LOCAL": 0, "A": 32, "B": 80, "CARD": 128, "AUX": 160}


def src_value(port, n):
    return _SRC_BASE[port] + n


def src_from_value(v):
    """Return (port, n) or a special tuple for non-physical sources."""
    if v <= 0:
        return ("OFF", 0)
    for port in ("LOCAL", "A", "B", "CARD", "AUX"):
        base = _SRC_BASE[port]
        if base < v <= base + PORT_SIZE[port]:
            return (port, v - base)
    if v == 167:
        return ("TB", 1)
    if v == 168:
        return ("TB", 2)
    return ("UOUT", v)  # output-type sources, only valid for user out


def src_label(v):
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
    """Source list for the output picker."""
    g = [
        {"name": "Outputs (main patch 1-16)", "items": [[168 + i, f"Output {i}"] for i in range(1, 17)]},
        {"name": "P16 Ultranet", "items": [[184 + i, f"P16 {i}"] for i in range(1, 17)]},
        {"name": "Aux Out / Monitor", "items": [[200 + i, f"Aux Out {i}"] for i in range(1, 7)] + [[207, "Monitor L"], [208, "Monitor R"]]},
    ]
    for port in ("LOCAL", "A", "B", "CARD", "AUX"):
        g.append({"name": f"{PORT_NAMES[port]} inputs (direct)",
                  "items": [[src_value(port, i), f"{PORT_NAMES[port]} {i}"] for i in range(1, PORT_SIZE[port] + 1)]})
    g.append({"name": "Other", "items": [[0, "Off"], [167, "Talkback Int"], [168, "Talkback Ext"]]})
    return g


# ---- Input blocks: /config/routing/IN/1-8 .. 25-32 ----
# value -> (kind, start). kind in LOCAL/A/B/CARD/UIN
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
    """Label for one slot of an output block option."""
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


def all_read_paths():
    paths = [p_in_block(b) for b in IN_BLOCKS] + [p_in_block("AUX")]
    for port in ("A", "B", "CARD"):
        paths += [p_out_block(port, b) for b in out_blocks(port)]
    paths += [p_uin(i) for i in range(1, 33)]
    paths += [p_uout(i) for i in range(1, 49)]
    paths += [p_ch_name(i) for i in range(1, 33)]
    paths += [p_aux_name(i) for i in range(1, 9)]
    return paths


# ---- Faders ----
FADERS = [f"ch{i}" for i in range(1, 33)] + [f"aux{i}" for i in range(1, 9)]


def fader_label(f):
    return f"Ch {f[2:]}" if f.startswith("ch") else f"Aux {f[3:]}"


def resolve_fader(f, st):
    """What feeds a fader. Returns dict: kind (port/UIN/USB/AUX), n, uin."""
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
    """What feeds physical output m on an AES50/Card port."""
    blocks = out_blocks(port)
    b = blocks[(m - 1) // 8]
    v = st.get(p_out_block(port, b))
    if v is None or not (0 <= v < len(OUT_BLOCK_OPTS)):
        return {"kind": "?", "label": "?"}
    kind, s = OUT_BLOCK_OPTS[v]
    n = s + (m - 1) % 8
    if kind == "UOUT":
        uv = st.get(p_uout(n))
        return {"kind": "UOUT", "n": n, "src": uv,
                "label": src_label(uv) if uv is not None else "?"}
    return {"kind": kind, "n": n, "label": out_block_slot_label(kind, n)}


# ---- Device presets ----
PRESETS = {
    "S32": {"name": "S32", "inPort": "A", "inStart": 1, "inCount": 32, "outPort": "A", "outStart": 1, "outCount": 16, "cols": 8},
    "S16": {"name": "S16", "inPort": "A", "inStart": 1, "inCount": 16, "outPort": "A", "outStart": 1, "outCount": 8, "cols": 8},
    "CARD": {"name": "Card", "inPort": "CARD", "inStart": 1, "inCount": 32, "outPort": "CARD", "outStart": 1, "outCount": 32, "cols": 8},
    "LOCAL": {"name": "X32 Local", "inPort": "LOCAL", "inStart": 1, "inCount": 32, "outPort": "", "outStart": 1, "outCount": 0, "cols": 8},
    "CUSTOM": {"name": "Custom", "inPort": "A", "inStart": 1, "inCount": 8, "outPort": "A", "outStart": 1, "outCount": 8, "cols": 8},
}
