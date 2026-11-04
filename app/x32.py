"""Minimal X32 OSC client (UDP 10023) with a state cache."""
import os
import socket
import struct
import threading
import time

import x32map

XREMOTE_EVERY = 5.0    # s between /xremote renewals (console timeout is 10 s)
REREAD_TIME = 120.0    # s between safety re-reads of all routing values
READBACK_DELAY = 0.5   # s after a set before it is read back
STALE_WINDOW = 0.4     # s after a set in which a different reply counts as stale


def _pad(b):
    return b + b"\0" * (4 - len(b) % 4)


def osc_encode(addr, *args):
    msg = _pad(addr.encode())
    tags = ","
    data = b""
    for a in args:
        if isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "s"
            data += _pad(str(a).encode())
    return msg + _pad(tags.encode()) + data


def _read_str(buf, i):
    end = buf.index(b"\0", i)
    s = buf[i:end].decode("utf-8", "replace")
    i = end + 1
    i += (4 - i % 4) % 4
    return s, i


def osc_decode(buf):
    addr, i = _read_str(buf, 0)
    if i >= len(buf):
        return addr, []
    tags, i = _read_str(buf, i)
    args = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack(">i", buf[i:i + 4])[0]); i += 4
        elif t == "f":
            args.append(struct.unpack(">f", buf[i:i + 4])[0]); i += 4
        elif t == "s":
            s, i = _read_str(buf, i); args.append(s)
        elif t == "b":
            n = struct.unpack(">i", buf[i:i + 4])[0]; i += 4 + n + (4 - n % 4) % 4
    return addr, args


class X32:
    def __init__(self, ip, mock=False):
        self.ip = ip
        self.mock = mock
        self.state = {}
        self.last_rx = 0.0
        self.info = ""
        self.lock = threading.Lock()
        self.version = 0                       # bumps on every state change
        self.changed = threading.Condition()   # notified on every bump
        self.port = int(os.environ.get("X32_PORT", "10023"))
        self.xinfo_args = None                 # last /xinfo reply from the console
        self.listeners = []                    # fn(addr, args, raw) for every console packet (relay)
        self.local_listeners = []              # fn(raw) for every set made by this app (relay)
        self.relevant = set(x32map.all_read_paths())
        self.recent = {}                       # addr -> (value, time) of our own recent sets
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(1.0)
        if mock:
            self._mock_init()
        threading.Thread(target=self._rx, daemon=True).start()
        threading.Thread(target=self._keepalive, daemon=True).start()

    # -- mock --
    def _mock_init(self):
        s = self.state
        s[x32map.p_in_block("1-8")] = 20      # User In 1-8
        s[x32map.p_in_block("9-16")] = 21
        s[x32map.p_in_block("17-24")] = 22
        s[x32map.p_in_block("25-32")] = 23
        s[x32map.p_in_block("AUX")] = 0
        for port in ("A", "B", "CARD"):
            for i, b in enumerate(x32map.out_blocks(port)):
                s[x32map.p_out_block(port, b)] = 25 + min(i, 5) if port == "A" else 20
        for i in range(1, 33):
            s[x32map.p_uin(i)] = x32map.src_value("A", i)
            s[x32map.p_ch_name(i)] = ""
        for i in range(1, 49):
            s[x32map.p_uout(i)] = 168 + ((i - 1) % 16) + 1
        for i in range(1, 9):
            s[x32map.p_aux_name(i)] = ""
        for i in range(1, 17):
            s[x32map.p_local_out("XLR", i)] = 3 + i  # Bus i
        for i in range(1, 7):
            s[x32map.p_local_out("AUXOUT", i)] = 0
        for i, b in enumerate(x32map.XLR_BLOCKS):
            s[x32map.p_xlr_block(b)] = x32map.XLR_BLOCK_OPTS.index(("OUT", i * 4 + 1))
        for f in x32map.FADERS:
            s[x32map.p_ins_sel(f)] = 0
            s[x32map.p_ins_on(f)] = 0
        for r, t in ((1, 0), (2, 10), (3, 28), (4, 38)):  # HALL, DLY, GEQ, LIM
            s[x32map.p_fx_type(r)] = t
        for r in range(5, 9):
            s[x32map.p_fx_type(r)] = 0  # GEQ2
        s[x32map.p_ch_name(1)] = "Kick"
        s[x32map.p_ch_name(2)] = "Snare"
        self.info = "MOCK X32"
        self.xinfo_args = ["127.0.0.1", "Mock X32", "X32", "4.06"]

    @property
    def connected(self):
        return self.mock or (time.time() - self.last_rx) < 12

    def set_ip(self, ip):
        self.ip = ip
        self.last_rx = 0
        self.refresh()

    def send(self, addr, *args):
        if self.mock:
            return  # set() stores the value
        if not self.ip:
            return
        try:
            self.sock.sendto(osc_encode(addr, *args), (self.ip, self.port))
        except OSError:
            pass

    def send_raw(self, data):
        """Forward a client packet to the console as-is (relay)."""
        if self.mock or not self.ip:
            return
        try:
            self.sock.sendto(data, (self.ip, self.port))
        except OSError:
            pass

    def bump(self):
        with self.changed:
            self.version += 1
            self.changed.notify_all()

    def _store(self, addr, value):
        with self.lock:
            same = self.state.get(addr) == value
            self.state[addr] = value
        if not same:
            self.bump()

    def set(self, addr, value):
        self.send(addr, value)
        self.recent[addr] = (value, time.time())
        self._store(addr, value)
        raw = osc_encode(addr, value)
        for fn in list(self.local_listeners):
            fn(raw)
        if not self.mock:
            # Read back later. The console can answer an instant read with the old value,
            # which made the page jump back until the next click.
            threading.Timer(READBACK_DELAY, self.send, (addr,)).start()

    def refresh(self):
        if self.mock:
            return
        threading.Thread(target=self._refresh, daemon=True).start()

    def _refresh(self):
        self.send("/xinfo")
        self.send("/info")
        self.send("/xremote")
        for p in x32map.all_read_paths():
            self.send(p)
            time.sleep(0.004)

    def _rx(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(1)
                continue
            try:
                addr, args = osc_decode(data)
            except Exception:
                continue
            was = self.connected
            self.last_rx = time.time()
            if addr == "/xinfo" and len(args) >= 4:
                self.xinfo_args = args
                self.info = f"{args[2]} \"{args[1]}\" fw {args[3]} at {args[0]}"
            elif addr == "/info":
                if not self.info.startswith(("X32", "M32")) or "fw" not in self.info:
                    self.info = " ".join(str(a) for a in args)
            elif args and addr in self.relevant:
                r = self.recent.get(addr)
                if r and time.time() - r[1] < STALE_WINDOW and args[0] != r[0]:
                    pass  # old value from before our own set: ignore
                else:
                    self._store(addr, args[0])
            for fn in list(self.listeners):
                try:
                    fn(addr, args, data)
                except Exception:
                    pass
            if not was:
                self.bump()
                self.refresh()

    def _keepalive(self):
        """Stay connected like X32-Mix / X32-Edit: renew /xremote well inside its 10 s timeout."""
        last_read = time.time()
        while True:
            if not self.mock and self.ip:
                was = self.connected
                self.send("/xremote")   # console pushes all changes to us for 10 s
                self.send("/xinfo")     # keeps last_rx fresh (connection check)
                if self.connected and time.time() - last_read > REREAD_TIME:
                    last_read = time.time()
                    self.refresh()      # safety re-read in its own thread (never delays /xremote)
                if was and not self.connected:
                    self.bump()
            time.sleep(XREMOTE_EVERY)

    def snapshot(self):
        with self.lock:
            return dict(self.state)


def discover(timeout=1.5):
    """Find consoles like X32-Edit does: broadcast /xinfo on UDP 10023."""
    targets = {"255.255.255.255"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                targets.add(".".join(ip.split(".")[:3]) + ".255")
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("0.0.0.0", 0))
    s.settimeout(0.2)
    for t in targets:
        try:
            s.sendto(osc_encode("/xinfo"), (t, 10023))
        except OSError:
            pass
    found = {}
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, src = s.recvfrom(4096)
            addr, args = osc_decode(data)
        except (socket.timeout, OSError, ValueError):
            continue
        if addr == "/xinfo" and len(args) >= 4:
            found[src[0]] = {"ip": src[0], "name": str(args[1]), "model": str(args[2]), "fw": str(args[3])}
    s.close()
    return list(found.values())
