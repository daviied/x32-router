"""OSC relay: the Pi looks like an X32 to mixing apps and is the console's only client.

- Apps (X32-Mix, Mixing Station, X32-Edit ...) connect to the Pi on UDP 10023.
- The console sees one client: the Pi's existing connection (one /xremote).
- Reads are answered from a cache when the value is known; else forwarded once.
- Writes are forwarded to the console and copied to the other apps.
- Console changes (desk, this web app) are copied to every app that sent /xremote.
- Meter / subscription streams are requested once and copied to each app that asked.
"""
import socket
import threading
import time

from x32 import osc_decode, osc_encode

CACHE_TTL = 60.0         # s a cached value is trusted (console pushes keep it current)
CLIENT_TIMEOUT = 30.0    # s without packets before an app is dropped
XREMOTE_TIME = 10.0      # s an app's /xremote lasts (same as the console)
SUB_TIME = 10.0          # s a meter subscription lasts without /renew
UPSTREAM_SUB_GAP = 4.0   # s minimum between identical upstream subscribe requests
PENDING_TIME = 2.0       # s a forwarded read waits for its reply
SUB_CMDS = {"/meters", "/batchsubscribe", "/formatsubscribe", "/subscribe"}
# Writes that can change many values at once (scene/snippet/preset loads, copy, text commands):
# clear the cache so the next reads come from the console.
ACTION_PREFIX = ("/-action", "/load", "/save", "/copy", "/add", "/delete", "/-show", "/-snap", "/-libs")


def _local_ip_for(peer_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_ip, 9))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


class Relay:
    def __init__(self, x32, port=10023, name="X32-Relay"):
        self.x = x32
        self.port = port
        self.name = name
        self.sock = None
        self.running = False
        self.error = ""
        self.lock = threading.Lock()
        self.clients = {}        # (ip, port) -> {"seen", "xremote", "rx", "tx"}
        self.cache = {}          # addr -> (raw, time)
        self.pending = {}        # addr -> {"clients": set, "t": time}
        self.node_q = []         # FIFO of (client, time) waiting for "node" replies
        self.subs = {}           # reply addr -> {"clients": {client: expiry}, "req": raw, "up": time}
        self.stats = {"fromApps": 0, "toConsole": 0, "fromConsole": 0, "toApps": 0, "cacheHits": 0}

    # ---------- lifecycle ----------
    def start(self):
        if self.running:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", self.port))
            s.settimeout(1.0)
        except OSError as e:
            self.error = f"Cannot open UDP {self.port}: {e}"
            return False
        self.sock, self.running, self.error = s, True, ""
        self.x.listeners.append(self._from_console)
        self.x.local_listeners.append(self._from_webapp)
        threading.Thread(target=self._rx, daemon=True).start()
        threading.Thread(target=self._housekeeping, daemon=True).start()
        return True

    def stop(self):
        self.running = False
        for lst, fn in ((self.x.listeners, self._from_console), (self.x.local_listeners, self._from_webapp)):
            if fn in lst:
                lst.remove(fn)
        if self.sock:
            self.sock.close()
            self.sock = None
        with self.lock:
            self.clients.clear(); self.cache.clear(); self.pending.clear(); self.subs.clear(); self.node_q.clear()

    def status(self):
        now = time.time()
        with self.lock:
            cl = [{"addr": f"{a[0]}:{a[1]}", "xremote": c["xremote"] > now, "idle": round(now - c["seen"], 1),
                   "rx": c["rx"], "tx": c["tx"]} for a, c in self.clients.items()]
            return {"running": self.running, "error": self.error, "port": self.port, "name": self.name,
                    "clients": cl, "cached": len(self.cache), "subs": len(self.subs), "stats": dict(self.stats)}

    # ---------- send helpers ----------
    def _to_app(self, client, raw):
        try:
            self.sock.sendto(raw, client)
        except (OSError, AttributeError):
            return
        c = self.clients.get(client)
        if c:
            c["tx"] += 1
        self.stats["toApps"] += 1

    def _to_console(self, raw):
        self.x.send_raw(raw)
        self.stats["toConsole"] += 1

    def _fanout(self, raw, exclude=None):
        now = time.time()
        for a, c in list(self.clients.items()):
            if a != exclude and c["xremote"] > now:
                self._to_app(a, raw)

    # ---------- apps -> relay ----------
    def _rx(self):
        while self.running:
            try:
                data, client = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._from_app(client, data)
            except Exception:
                pass

    def _from_app(self, client, data):
        now = time.time()
        self.stats["fromApps"] += 1
        with self.lock:
            c = self.clients.setdefault(client, {"seen": now, "xremote": 0.0, "rx": 0, "tx": 0})
            c["seen"] = now
            c["rx"] += 1
        try:
            addr, args = osc_decode(data)
        except Exception:
            self._to_console(data)  # unknown format: pass through
            return

        if addr in ("/xremote", "/xremotenfb"):
            c["xremote"] = now + XREMOTE_TIME
            return
        if addr in ("/xinfo", "/status", "/info"):
            # Discovery / hello: answer as the relay, with the Pi's IP. The console's own reply
            # carries the console IP, so apps listed the console and never the Pi.
            self._to_app(client, self._hello(addr, client))
            return
        if addr in SUB_CMDS:
            self._subscribe(client, addr, args, data, now)
            return
        if addr == "/renew":
            name = str(args[0]) if args else ""
            with self.lock:
                for key, sub in self.subs.items():
                    if client in sub["clients"] and (not name or key == name or key.startswith(name)):
                        sub["clients"][client] = now + SUB_TIME
            return
        if addr == "/unsubscribe":
            with self.lock:
                for sub in self.subs.values():
                    sub["clients"].pop(client, None)
            return
        if addr == "/node":
            with self.lock:
                self.node_q.append((client, now))
            self._to_console(data)
            return
        if not args:  # read
            self._get(client, addr, data, now)
            return
        # write (or action)
        if addr == "/" or addr.startswith(ACTION_PREFIX):
            with self.lock:
                self.cache.clear()
                self.pending.setdefault(addr, {"clients": set(), "t": now})["clients"].add(client)
            self._to_console(data)
            return
        self._to_console(data)
        with self.lock:
            self.cache[addr] = (data, now)
        if addr in self.x.relevant:
            self.x._store(addr, args[0])
        self._fanout(data, exclude=client)

    def _hello(self, addr, client):
        xa = self.x.xinfo_args or []
        model = str(xa[2]) if len(xa) > 2 else "X32"
        fw = str(xa[3]) if len(xa) > 3 else "4.06"
        ip = _local_ip_for(client[0])
        if addr == "/xinfo":
            return osc_encode("/xinfo", ip, self.name, model, fw)
        if addr == "/status":
            return osc_encode("/status", "active", ip, self.name)
        return osc_encode("/info", "V2.07", self.name, model, fw)

    def _get(self, client, addr, data, now):
        with self.lock:
            hit = self.cache.get(addr)
            if hit and now - hit[1] < CACHE_TTL and self.x.connected:
                self.stats["cacheHits"] += 1
                raw = hit[0]
            else:
                raw = None
                p = self.pending.get(addr)
                fresh = p and now - p["t"] < PENDING_TIME
                if not fresh:
                    p = self.pending[addr] = {"clients": set(), "t": now}
                p["clients"].add(client)
        if raw:
            self._to_app(client, raw)
        elif not fresh:
            self._to_console(data)

    def _subscribe(self, client, addr, args, data, now):
        # reply address: /meters -> args[0]; batch/format -> alias args[0]; /subscribe -> args[0]
        key = str(args[0]) if args else addr
        with self.lock:
            sub = self.subs.setdefault(key, {"clients": {}, "req": data, "up": 0.0})
            sub["clients"][client] = now + SUB_TIME
            send = sub["req"] != data or now - sub["up"] > UPSTREAM_SUB_GAP
            if send:
                sub["req"], sub["up"] = data, now
        if send:
            self._to_console(data)

    # ---------- console -> relay ----------
    def _from_console(self, addr, args, raw):
        if not self.running:
            return
        now = time.time()
        self.stats["fromConsole"] += 1
        targets = []
        with self.lock:
            if addr == "node":
                while self.node_q and now - self.node_q[0][1] > PENDING_TIME * 2:
                    self.node_q.pop(0)
                if self.node_q:
                    targets = [self.node_q.pop(0)[0]]
                kind = "reply"
            elif addr in self.subs:
                sub = self.subs[addr]
                targets = [a for a, exp in sub["clients"].items() if exp > now]
                kind = "stream"
            else:
                p = self.pending.pop(addr, None)
                old = self.cache.get(addr)
                changed = not old or old[0] != raw
                if addr.startswith("/") and args:
                    self.cache[addr] = (raw, now)
                if p:
                    targets = list(p["clients"])
                    kind = "reply"
                elif changed:
                    kind = "push"
                else:
                    return  # re-read with the same value: apps already have it
        if kind == "push":
            self._fanout(raw)
            return
        for a in targets:
            self._to_app(a, raw)

    def _from_webapp(self, raw):
        """The web app changed routing: tell the mixing apps (the console does not echo it to us)."""
        if not self.running:
            return
        try:
            addr, _ = osc_decode(raw)
            with self.lock:
                self.cache[addr] = (raw, time.time())
        except Exception:
            pass
        self._fanout(raw)

    # ---------- upkeep ----------
    def _housekeeping(self):
        while self.running:
            time.sleep(2)
            now = time.time()
            renew = []
            with self.lock:
                for a in [a for a, c in self.clients.items() if now - c["seen"] > CLIENT_TIMEOUT]:
                    del self.clients[a]
                for k in [k for k, p in self.pending.items() if now - p["t"] > PENDING_TIME * 5]:
                    del self.pending[k]
                for key in list(self.subs):
                    sub = self.subs[key]
                    sub["clients"] = {a: e for a, e in sub["clients"].items() if e > now}
                    if not sub["clients"]:
                        del self.subs[key]
                    elif now - sub["up"] > UPSTREAM_SUB_GAP * 2:
                        sub["up"] = now
                        renew.append(sub["req"])  # keep the console stream alive while apps want it
                if not self.x.connected:
                    self.cache.clear()
            for raw in renew:
                self._to_console(raw)
