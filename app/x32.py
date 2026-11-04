"""Minimal X32 OSC client (UDP 10023) with a state cache."""
import socket
import struct
import threading
import time

import x32map


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
        for f in x32map.FADERS:
            s[x32map.p_ins_sel(f)] = 0
            s[x32map.p_ins_on(f)] = 0
        for r, t in ((1, 0), (2, 10), (3, 28), (4, 38)):  # HALL, DLY, GEQ, LIM
            s[x32map.p_fx_type(r)] = t
        s[x32map.p_ch_name(1)] = "Kick"
        s[x32map.p_ch_name(2)] = "Snare"
        self.info = "MOCK X32"

    @property
    def connected(self):
        return self.mock or (time.time() - self.last_rx) < 12

    def set_ip(self, ip):
        self.ip = ip
        self.last_rx = 0
        self.refresh()

    def send(self, addr, *args):
        if self.mock:
            if args:
                with self.lock:
                    self.state[addr] = args[0]
            return
        if not self.ip:
            return
        try:
            self.sock.sendto(osc_encode(addr, *args), (self.ip, 10023))
        except OSError:
            pass

    def set(self, addr, value):
        self.send(addr, value)
        with self.lock:
            self.state[addr] = value
        if not self.mock:
            self.send(addr)  # read back to confirm

    def refresh(self):
        if self.mock:
            return
        threading.Thread(target=self._refresh, daemon=True).start()

    def _refresh(self):
        self.send("/info")
        self.send("/xremote")
        for p in x32map.all_read_paths():
            self.send(p)
            time.sleep(0.004)

    def _rx(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
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
            if addr == "/info":
                self.info = " ".join(str(a) for a in args)
            elif args:
                with self.lock:
                    self.state[addr] = args[0]
            if not was:
                self.refresh()

    def _keepalive(self):
        n = 0
        while True:
            if not self.mock:
                self.send("/xremote")
                self.send("/info")
                if n % 6 == 0:  # full re-read every ~48 s
                    self._refresh()
            n += 1
            time.sleep(8)

    def snapshot(self):
        with self.lock:
            return dict(self.state)
