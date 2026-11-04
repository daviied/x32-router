"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

let META = null, S = null, timer = null;
let mode = "in", tab = "route";
let selFader = null, selJack = null;   // inputs: {dev, jack}
let selOut = null;                      // outputs: {dev, jack}
let devDraft = null;                    // devices tab edit copy

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Requested-With": "x32r" },
  };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Login required"); }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(t._h); t._h = setTimeout(() => (t.className = "toast"), bad ? 4500 : 2500);
}

// ---------- auth ----------
function showLogin() { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); clearInterval(timer); }
$("#loginForm").onsubmit = async (e) => {
  e.preventDefault();
  try { await api("/api/login", { password: $("#pw").value }); $("#pw").value = ""; start(); }
  catch (err) { $("#loginErr").textContent = err.message; }
};
$("#logout").onclick = async () => { await api("/api/logout", {}); showLogin(); };

async function start() {
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  META = await api("/api/meta");
  const ps = $("#newPreset"); ps.innerHTML = "";
  for (const k of Object.keys(META.presets)) ps.append(new Option(META.presets[k].name + " preset", k));
  await poll();
  clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) poll(); }, 2000);
}

async function poll() {
  try { S = await api("/api/state"); render(); } catch (e) { /* keep last view */ }
}

// ---------- tabs ----------
$("#tabs").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  tab = b.dataset.tab;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + tab));
  if (tab === "devices") devDraft = null;
  render();
};
$("#mode").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  mode = b.dataset.mode;
  document.querySelectorAll("#mode button").forEach((x) => x.classList.toggle("on", x === b));
  $("#routeIn").classList.toggle("hidden", mode !== "in");
  $("#routeOut").classList.toggle("hidden", mode !== "out");
  render();
};

function render() {
  if (!S) return;
  $("#dot").classList.toggle("on", S.connected);
  $("#dot").title = S.connected ? "X32 connected" : "X32 not connected";
  if (tab === "route") mode === "in" ? renderIn() : renderOut();
  if (tab === "blocks") renderBlocks();
  if (tab === "devices" && !devDraft) renderDevices();
  if (tab === "settings") renderSettings();
}

// ---------- inputs ----------
function renderIn() {
  const fd = $("#faders"); fd.innerHTML = "";
  const selF = S.faders.find((f) => f.id === selFader);
  for (const f of S.faders) {
    const b = el("button", "fader" + (f.id.startsWith("aux") ? " aux" : "") + (f.id === selFader ? " sel" : "") + (f.routable ? "" : " lock"));
    b.append(el("span", "num", f.label), el("span", "nm", f.name || " "), el("span", "src", f.src + (f.via ? " · " + f.via.replace("User In", "UIn") : "")));
    b.title = `${f.label}${f.name ? " " + f.name : ""}\nSource: ${f.src}${f.via ? "\nVia: " + f.via : ""}${f.routable ? "" : "\nNot via User In: change on Blocks tab"}`;
    b.onclick = () => pickFader(f);
    fd.append(b);
  }
  const bx = $("#boxesIn"); bx.innerHTML = "";
  for (const d of S.devices) {
    if (!d.inCount) continue;
    const used = S.jacksIn[d.id] || {};
    const box = boxShell(d, `${META.portNames[d.inPort]} ${d.inStart}-${d.inStart + d.inCount - 1}`);
    const grid = box.querySelector(".jacks");
    for (let j = 1; j <= d.inCount; j++) {
      const fs = used[j] || [];
      const isSel = selJack && selJack.dev === d.id && selJack.jack === j;
      const hl = selF && fs.includes(selF.id);
      const jk = jackEl(j, fs.map(shortFader).join(","), "in" + (fs.length ? " used" : "") + (isSel ? " sel" : "") + (hl ? " hl" : ""));
      jk.title = `${d.name} In ${j} = ${META.portNames[d.inPort]} ${d.inStart + j - 1}` + (fs.length ? `\nOn: ${fs.map(shortFader).join(", ")}` : "");
      jk.onclick = () => pickJack(d, j);
      grid.append(jk);
    }
    bx.append(box);
  }
  if (!S.devices.some((d) => d.inCount)) bx.append(el("p", "note", "No input devices. Add one on the Devices tab."));
  $("#hint").textContent = selF ? `${selF.label} selected. Click a stage box input.` : selJack ? "Input selected. Click a fader." : "Select a fader, then a stage box input.";
}

const shortFader = (id) => id.startsWith("ch") ? id.slice(2) : "A" + id.slice(3);

function boxShell(d, sub) {
  const box = el("div", "box");
  const t = el("div", "title"); t.append(el("span", null, d.name), el("small", null, sub));
  const g = el("div", "jacks"); g.style.gridTemplateColumns = `repeat(${d.cols || 8}, auto)`;
  box.append(t, g);
  return box;
}

function jackEl(n, tag, cls) {
  const b = el("button", "jack " + cls);
  b.append(el("span", "xlr"), el("span", "n", n), el("span", "tag", tag || ""));
  return b;
}

function pickFader(f) {
  if (!f.routable) { toast(`${f.label} source is ${f.src}. It is not via User In. Change its block on the Blocks tab.`, true); }
  if (selJack && f.routable) { doRouteIn(f.id, selJack.dev, selJack.jack); selJack = null; return; }
  selFader = selFader === f.id ? null : f.id;
  renderIn();
}

function pickJack(d, j) {
  if (selFader) { doRouteIn(selFader, d.id, j); return; }
  selJack = selJack && selJack.dev === d.id && selJack.jack === j ? null : { dev: d.id, jack: j };
  renderIn();
}

async function doRouteIn(fader, dev, jack) {
  try {
    const r = await api("/api/route/input", { fader, device: dev, jack });
    toast(r.msg);
    if ($("#advance").checked) {
      const i = S.faders.findIndex((f) => f.id === fader);
      const nx = S.faders.slice(i + 1).find((f) => f.routable);
      selFader = nx ? nx.id : null;
    } else selFader = null;
    await poll();
  } catch (e) { toast(e.message, true); }
}

// ---------- outputs ----------
function renderOut() {
  const bx = $("#boxesOut"); bx.innerHTML = "";
  let cur = null;
  for (const d of S.devices) {
    if (!d.outPort || !d.outCount) continue;
    const outs = S.jacksOut[d.id] || {};
    const box = boxShell(d, `${META.portNames[d.outPort]} Out ${d.outStart}-${d.outStart + d.outCount - 1}`);
    const grid = box.querySelector(".jacks");
    for (let j = 1; j <= d.outCount; j++) {
      const o = outs[j] || { label: "?", routable: false };
      const isSel = selOut && selOut.dev === d.id && selOut.jack === j;
      if (isSel) cur = o;
      const jk = jackEl(j, o.label, "out used" + (isSel ? " sel" : "") + (o.routable ? "" : " lock"));
      jk.title = `${d.name} Out ${j} = ${META.portNames[d.outPort]} Out ${d.outStart + j - 1}\nSource: ${o.label}${o.via ? "\nVia: " + o.via : "\nNot via User Out: change on Blocks tab"}`;
      jk.onclick = () => {
        if (!o.routable) toast(`Source is fixed by the output block (${o.label}). Change it on the Blocks tab.`, true);
        selOut = isSel ? null : { dev: d.id, jack: j };
        renderOut();
      };
      grid.append(jk);
    }
    bx.append(box);
  }
  if (!S.devices.some((d) => d.outPort && d.outCount)) bx.append(el("p", "note", "No output devices. Add one on the Devices tab."));

  const sw = $("#sources");
  const open = new Set([...sw.querySelectorAll("details[open]")].map((x) => x.dataset.k));
  sw.innerHTML = "";
  META.uoutSources.forEach((g, gi) => {
    const det = el("details"); det.dataset.k = gi;
    if (open.has(String(gi)) || (!sw.dataset.init && gi === 0)) det.open = true;
    det.append(el("summary", null, g.name));
    const grid = el("div", "srcgrid");
    for (const [v, label] of g.items) {
      const b = el("button", cur && cur.label === label ? "cur" : "", label);
      b.onclick = () => doRouteOut(v);
      grid.append(b);
    }
    det.append(grid); sw.append(det);
  });
  sw.dataset.init = "1";
  $("#hint").textContent = selOut ? "Output selected. Click a source." : "Select a stage box output, then a source.";
}

async function doRouteOut(source) {
  if (!selOut) { toast("Select a stage box output first.", true); return; }
  try {
    const r = await api("/api/route/output", { device: selOut.dev, jack: selOut.jack, source });
    toast(r.msg);
    if ($("#advance").checked) {
      const d = S.devices.find((x) => x.id === selOut.dev);
      selOut = d && selOut.jack < d.outCount ? { dev: d.id, jack: selOut.jack + 1 } : null;
    } else selOut = null;
    await poll();
  } catch (e) { toast(e.message, true); }
}

// ---------- blocks ----------
function blockSelect(b, opts) {
  const lab = el("label", null, b.label);
  const s = el("select");
  if (b.value == null) s.append(new Option("(unknown)", ""));
  for (const [v, t] of opts) s.append(new Option(t, v));
  s.value = b.value == null ? "" : String(b.value);
  s.onchange = async () => {
    try { await api("/api/block", { path: b.path, value: Number(s.value) }); toast(`${b.label} → ${s.selectedOptions[0].text}`); poll(); }
    catch (e) { toast(e.message, true); }
  };
  lab.append(s);
  return lab;
}

function renderBlocks() {
  if (document.activeElement && document.activeElement.tagName === "SELECT") return;
  const ib = $("#inBlocks"); ib.innerHTML = "";
  for (const b of S.blocks.in) ib.append(blockSelect(b, META.inOpts));
  ib.append(blockSelect(S.blocks.aux, META.auxOpts));
  const ob = $("#outBlocks"); ob.innerHTML = "";
  const used = new Set(S.devices.filter((d) => d.outPort && d.outCount).map((d) => d.outPort));
  for (const port of ["A", "B", "CARD"]) {
    const det = el("details"); det.open = used.has(port);
    det.append(el("summary", null, META.portNames[port] + " outputs" + (used.has(port) ? "" : " (no device)")));
    const g = el("div", "blockgrid");
    for (const b of S.blocks.out[port]) g.append(blockSelect(b, META.outOpts));
    det.append(g); ob.append(det);
  }
}

$("#allUin").onclick = async () => {
  const uinStart = META.inOpts.findIndex(([, t]) => t.startsWith("User In"));
  try {
    for (let i = 0; i < 4; i++) await api("/api/block", { path: S.blocks.in[i].path, value: uinStart + i });
    toast("Ch 1-32 now use User In 1-32"); poll();
  } catch (e) { toast(e.message, true); }
};

// ---------- devices ----------
const PORT_OPTS_IN = ["LOCAL", "A", "B", "CARD", "AUX"];
const PORT_OPTS_OUT = ["", "A", "B", "CARD"];

function renderDevices() {
  devDraft = JSON.parse(JSON.stringify(S.devices));
  drawDevices();
}

function drawDevices() {
  const L = $("#devList"); L.innerHTML = "";
  devDraft.forEach((d, i) => {
    const c = el("div", "card dev");
    const head = el("div", "head");
    const nm = el("input"); nm.value = d.name; nm.oninput = () => (d.name = nm.value);
    const del = el("button", "danger", "Remove"); del.onclick = () => { devDraft.splice(i, 1); drawDevices(); };
    head.append(nm, del); c.append(head);
    c.append(
      field("Input port", sel(PORT_OPTS_IN, d, "inPort", (p) => META.portNames[p])),
      field("First input (port #)", num(d, "inStart")),
      field("Input count", num(d, "inCount")),
      field("Output port", sel(PORT_OPTS_OUT, d, "outPort", (p) => p ? META.portNames[p] : "None")),
      field("First output (port #)", num(d, "outStart")),
      field("Output count", num(d, "outCount")),
      field("Jacks per row", num(d, "cols")),
    );
    L.append(c);
  });
}
function field(t, input) { const l = el("label", null, t); l.append(input); return l; }
function num(d, k) { const i = el("input"); i.type = "number"; i.min = 0; i.value = d[k]; i.oninput = () => (d[k] = Number(i.value)); return i; }
function sel(opts, d, k, lab) {
  const s = el("select"); for (const o of opts) s.append(new Option(lab(o), o));
  s.value = d[k] || ""; s.onchange = () => (d[k] = s.value); return s;
}
$("#addDev").onclick = () => {
  if (!devDraft) renderDevices();
  const k = $("#newPreset").value;
  devDraft.push({ ...META.presets[k], preset: k, id: "" });
  drawDevices();
};
$("#saveDevs").onclick = async () => {
  try { await api("/api/config", { devices: devDraft }); toast("Devices saved"); devDraft = null; await poll(); }
  catch (e) { toast(e.message, true); }
};

// ---------- settings ----------
function renderSettings() {
  const ip = $("#ip"); if (document.activeElement !== ip) ip.value = S.x32Ip || "";
  $("#status").textContent = S.mock ? "Mock mode (no mixer)." : S.connected ? `Connected: ${S.info}` : "Not connected. Check the IP address and network.";
}
$("#saveIp").onclick = async () => { try { await api("/api/config", { x32Ip: $("#ip").value }); toast("Saved"); poll(); } catch (e) { toast(e.message, true); } };
$("#refresh").onclick = async () => { await api("/api/refresh", {}); toast("Reading X32…"); setTimeout(poll, 1500); };

// ---------- boot ----------
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
fetch("/api/auth").then((r) => r.json()).then((j) => (j.authed ? start() : showLogin())).catch(showLogin);
