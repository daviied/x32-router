"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

let META = null, S = null, timer = null;
let mode = "in", tab = "route";
let selFader = null;   // inputs + inserts
let selJack = null;    // inputs: {dev, jack}
let selSlot = null;    // inserts: {dev, slot}
let selOut = null;     // outputs: {dev, jack}
let devDraft = null;   // devices tab edit copy

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
  selJack = selSlot = selOut = null;
  document.querySelectorAll("#mode button").forEach((x) => x.classList.toggle("on", x === b));
  $("#faderWrap").classList.toggle("hidden", mode === "out" || mode === "patch");
  $("#routePatch").classList.toggle("hidden", mode !== "patch");
  $("#advance").parentElement.classList.toggle("hidden", mode === "patch");
  $("#routeIn").classList.toggle("hidden", mode !== "in");
  $("#routeIns").classList.toggle("hidden", mode !== "ins");
  $("#routeOut").classList.toggle("hidden", mode !== "out");
  render();
};

function render() {
  if (!S) return;
  $("#dot").classList.toggle("on", S.connected);
  $("#dot").title = S.connected ? "X32 connected" : "X32 not connected";
  if (tab === "route") {
    if (mode === "patch") { renderPatch(); $("#hint").textContent = "Drag cables between jacks."; }
    else { renderFaders(); mode === "in" ? renderIn() : mode === "ins" ? renderIns() : renderOut(); }
    renderClear();
  }
  if (tab === "blocks") renderBlocks();
  if (tab === "devices" && !devDraft) renderDevices();
  if (tab === "settings") renderSettings();
  if (tab === "plan") renderPlan();
}

const devs = (kind) => S.devices.filter((d) => (d.kind || "box") === kind);
const fader = (id) => S.faders.find((f) => f.id === id);
const shortFader = (id) => id.startsWith("ch") ? id.slice(2) : "A" + id.slice(3);

// ---------- unassign button ----------
function renderClear() {
  const b = $("#clear");
  const f = fader(selFader);
  let show = false;
  if (mode === "in") { show = !!(f && f.routable); b.textContent = "Unassign input"; }
  if (mode === "ins") { show = !!(f && f.ins); b.textContent = "Remove insert"; }
  if (mode === "out") { const o = curOut(); show = !!(o && o.routable); b.textContent = "Unassign output"; }
  b.classList.toggle("hidden", !show);
  const fx = $("#fxins");
  fx.classList.toggle("hidden", !(f && (mode === "in" || mode === "ins") && !(f.ins && f.insOn)));
  fx.title = "Put the next free insert slot (outboard or internal FX) on this fader";
}
$("#fxins").onclick = async () => {
  try {
    const r = await api("/api/route/fxinsert", { fader: selFader });
    toast(r.msg, r.warn); await poll();
  } catch (e) { toast(e.message, true); }
};
$("#clear").onclick = async () => {
  try {
    let r;
    if (mode === "in") r = await api("/api/route/input", { fader: selFader });
    else if (mode === "ins") r = await api("/api/route/insert", { fader: selFader });
    else r = await api("/api/route/output", { device: selOut.dev, jack: selOut.jack, source: 0 });
    toast(r.msg); await poll();
  } catch (e) { toast(e.message, true); }
};

// ---------- faders (inputs + inserts) ----------
function renderFaders() {
  if (mode === "out") return;
  const fd = $("#faders"); fd.innerHTML = "";
  for (const f of S.faders) {
    const lock = mode === "in" && !f.routable;
    const b = el("button", "fader" + (f.id.startsWith("aux") ? " aux" : "") + (f.id === selFader ? " sel" : "") + (lock ? " lock" : ""));
    b.append(el("span", "num", f.label), el("span", "nm", f.name || " "));
    if (mode === "in") {
      b.append(el("span", "src", f.src + (f.via ? " · " + f.via.replace("User In", "UIn") : "")));
      if (f.plug) b.append(el("span", "ins", f.plug));
    }
    else b.append(el("span", f.ins ? "ins" : "src", f.ins ? f.ins + (f.insOn ? "" : " (off)") : "No insert"));
    b.title = `${f.label}${f.name ? " " + f.name : ""}\nSource: ${f.src}${f.plug ? " (" + f.plug + ")" : ""}${f.via ? "\nVia: " + f.via : ""}` +
      (f.ins ? `\nInsert: ${f.ins}${f.insOn ? "" : " (off)"}` : "") + (lock ? "\nNot via User In: change on Blocks tab" : "");
    b.onclick = () => pickFader(f);
    fd.append(b);
  }
}

function pickFader(f) {
  if (mode === "in") {
    if (!f.routable) toast(`${f.label} source is ${f.src}. It is not via User In. Change its block on the Blocks tab.`, true);
    if (selJack && f.routable) { const j = selJack; selJack = null; doRouteIn(f.id, j.dev, j.jack); return; }
  } else if (selSlot) { const s = selSlot; selSlot = null; doInsert(f.id, s.dev, s.slot); return; }
  selFader = selFader === f.id ? null : f.id;
  render();
}

function nextFader(id, routableOnly) {
  const i = S.faders.findIndex((f) => f.id === id);
  const nx = S.faders.slice(i + 1).find((f) => !routableOnly || f.routable);
  return nx ? nx.id : null;
}

// ---------- inputs ----------
function renderIn() {
  const bx = $("#boxesIn"); bx.innerHTML = "";
  const selF = fader(selFader);
  const list = devs("box").filter((d) => d.inCount);
  for (const d of list) {
    const used = S.jacksIn[d.id] || {};
    const box = boxShell(d, `${META.portNames[d.inPort]} ${d.inStart}-${d.inStart + d.inCount - 1}`);
    const grid = box.querySelector(".jacks");
    for (let j = 1; j <= d.inCount; j++) {
      const fs = used[j] || [];
      const isSel = selJack && selJack.dev === d.id && selJack.jack === j;
      const hl = selF && fs.includes(selF.id);
      const L = lbl(d, "in", j);
      const jk = jackEl(j, fs.map(shortFader).join(","), "in" + (fs.length ? " used" : "") + (isSel ? " sel" : "") + (hl ? " hl" : ""), L);
      jk.title = `${d.name} In ${j}${L ? " (" + L + ")" : ""} = ${META.portNames[d.inPort]} ${d.inStart + j - 1}` + (fs.length ? `\nOn: ${fs.map(shortFader).join(", ")}` : "");
      jk.onclick = () => pickJack(d, j);
      grid.append(jk);
    }
    bx.append(box);
  }
  if (!list.length) bx.append(el("p", "note", "No input devices. Add one on the Devices tab."));
  $("#hint").textContent = selF ? `${selF.label} selected. Click a stage box input.` : selJack ? "Input selected. Click a fader." : "Select a fader, then a stage box input.";
}

function boxShell(d, sub) {
  const box = el("div", "box");
  const t = el("div", "title"); t.append(el("span", null, d.name), el("small", null, sub));
  const g = el("div", "jacks"); g.style.gridTemplateColumns = `repeat(${d.cols || 8}, auto)`;
  box.append(t, g);
  return box;
}

function jackEl(n, tag, cls, label) {
  const b = el("button", "jack " + cls);
  b.append(el("span", "xlr"), el("span", "n", n), el("span", "lbl", label || ""), el("span", "tag", tag || ""));
  return b;
}
const lbl = (d, k, j) => ((d.labels || {})[k] || {})[j] || "";

function pickJack(d, j) {
  if (selFader) { doRouteIn(selFader, d.id, j); return; }
  selJack = selJack && selJack.dev === d.id && selJack.jack === j ? null : { dev: d.id, jack: j };
  render();
}

async function doRouteIn(fid, dev, jack) {
  try {
    const r = await api("/api/route/input", { fader: fid, device: dev, jack });
    toast(r.msg);
    selFader = $("#advance").checked ? nextFader(fid, true) : null;
    await poll();
  } catch (e) { toast(e.message, true); }
}

// ---------- inserts ----------
function renderIns() {
  const bx = $("#boxesIns"); bx.innerHTML = "";
  const selF = fader(selFader);
  const list = devs("insert").filter((d) => d.insCount);
  for (const d of list) {
    const used = S.slots[d.id] || {};
    const t = META.insTypes[d.insType];
    const box = boxShell(d, `${t.name} ${d.insStart}-${d.insStart + d.insCount - 1}`);
    const grid = box.querySelector(".jacks");
    for (let s = 1; s <= d.insCount; s++) {
      const fs = used[s] || [];
      const isSel = selSlot && selSlot.dev === d.id && selSlot.slot === s;
      const b = el("button", "slot" + (fs.length ? " used" : "") + (isSel ? " sel" : "") + (selF && fs.includes(selF.id) ? " hl" : ""));
      const pair = el("span", "pair");
      for (const lab of d.insType === "AUX" ? ["Send", "Ret"] : ["In", "Out"]) { const x = el("span"); x.append(el("i"), lab); pair.append(x); }
      const n = d.insStart + s - 1;
      const point = d.insType === "AUX" ? `Aux ${n}` : `FX${Math.ceil(n / 2)}${n % 2 ? "L" : "R"}`;
      const L = lbl(d, "ins", s);
      const inf = ((S.slotInfo || {})[d.id] || {})[s] || {};
      if (inf.warn) b.classList.add("warn");
      b.append(pair, el("span", "n", `${s} · ${point}`), el("span", "lbl", L || inf.fx || ""), el("span", "tag", fs.map(shortFader).join(",")));
      b.title = `${d.name} ${s}${L ? " (" + L + ")" : ""} = insert point ${point}` +
        (d.insType === "AUX" ? `\nSend: Aux Out ${n}${inf.send ? " → " + inf.send : ""}\nReturn: Aux In ${n}${inf.ret ? " ← " + inf.ret : ""}` : `\nLoaded: ${inf.fx || "?"}`) +
        (inf.warn ? "\n⚠ " + inf.warn : "") + (fs.length ? `\nOn: ${fs.map(shortFader).join(", ")}` : "");
      b.onclick = () => {
        if (selFader) { doInsert(selFader, d.id, s); return; }
        selSlot = isSel ? null : { dev: d.id, slot: s }; render();
      };
      grid.append(b);
    }
    bx.append(box);
  }
  if (!list.length) bx.append(el("p", "note", "No insert devices. Add an insert preset on the Devices tab."));
  $("#hint").textContent = selF ? `${selF.label} selected. Click an insert slot.` : selSlot ? "Slot selected. Click a fader." : "Select a fader, then an insert slot.";
}

async function doInsert(fid, dev, slot) {
  try {
    const r = await api("/api/route/insert", { fader: fid, device: dev, slot });
    toast(r.msg, r.warn);
    selFader = $("#advance").checked ? nextFader(fid, false) : null;
    await poll();
  } catch (e) { toast(e.message, true); }
}

// ---------- outputs ----------
function curOut() {
  if (!selOut) return null;
  return (S.jacksOut[selOut.dev] || {})[selOut.jack] || null;
}

function renderOut() {
  const bx = $("#boxesOut"); bx.innerHTML = "";
  const list = devs("box").filter((d) => d.outPort && d.outCount);
  for (const d of list) {
    const outs = S.jacksOut[d.id] || {};
    const box = boxShell(d, `${META.portNames[d.outPort]} ${d.outStart}-${d.outStart + d.outCount - 1}`);
    const grid = box.querySelector(".jacks");
    for (let j = 1; j <= d.outCount; j++) {
      const o = outs[j] || { label: "?", routable: false };
      const isSel = selOut && selOut.dev === d.id && selOut.jack === j;
      const L = lbl(d, "out", j);
      const jk = jackEl(j, o.label, "out" + (o.src ? " used" : "") + (isSel ? " sel" : "") + (o.routable ? "" : " lock"), L);
      jk.title = `${d.name} Out ${j}${L ? " (" + L + ")" : ""} = ${META.portNames[d.outPort]} ${d.outStart + j - 1}\nSource: ${o.label}` +
        (o.via ? "\nVia: " + o.via : o.pick === "mix" ? "" : "\nNot via User Out: change on Blocks tab");
      jk.onclick = () => {
        if (!o.routable) toast(`Source is fixed by the output block (${o.label}). Change it on the Blocks tab.`, true);
        selOut = isSel ? null : { dev: d.id, jack: j };
        render();
      };
      grid.append(jk);
    }
    bx.append(box);
  }
  if (!list.length) bx.append(el("p", "note", "No output devices. Add one on the Devices tab."));

  const cur = curOut();
  const pick = cur && cur.routable ? cur.pick : "";
  const sw = $("#sources");
  const open = new Set([...sw.querySelectorAll("details[open]")].map((x) => x.dataset.k));
  const firstTime = sw.dataset.pick !== pick;
  sw.innerHTML = ""; sw.dataset.pick = pick;
  if (!pick) sw.append(el("p", "note", "Select an output to see its sources."));
  const groups = pick === "mix" ? META.mixSources : pick === "uout" ? META.uoutSources : [];
  groups.forEach((g, gi) => {
    const k = pick + gi;
    const hasCur = g.items.some(([v]) => v === cur.src);
    const det = el("details"); det.dataset.k = k;
    det.open = firstTime ? gi === 0 || hasCur : open.has(k);
    det.append(el("summary", null, g.name));
    const grid = el("div", "srcgrid");
    for (const [v, label] of g.items) {
      const b = el("button", v === cur.src ? "cur" : "", label);
      b.onclick = () => doRouteOut(v);
      grid.append(b);
    }
    det.append(grid); sw.append(det);
  });
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

// ---------- patch bay ----------
const SRC_BASE = { LOCAL: 0, A: 32, B: 80, CARD: 128, AUX: 160 };
const CABLE_COLORS = ["#ff5a5a", "#ffb23f", "#f5e04a", "#3fd47a", "#3fa7ff", "#b77cff", "#ff7ccf", "#e7e9ee"];
let patchKey = "", drag = null, tapSel = null;

function srcToDevIn(v) {
  for (const [port, base] of Object.entries(SRC_BASE)) {
    if (v > base && v <= base + META.portSize[port]) {
      const n = v - base;
      const d = devs("box").find((x) => x.inPort === port && x.inCount && n >= x.inStart && n < x.inStart + x.inCount);
      if (d) return `devin:${d.id}:${n - d.inStart + 1}`;
    }
  }
  return null;
}

const MIX_PATCH = () => META.mixSources[0].items;  // Main / Bus / Matrix
const UO_PATCH = () => META.uoutSources.slice(0, 3).flatMap((g) => g.items);  // Output 1-16, P16, Aux/Mon

function patchCables() {
  const c = [];
  if ($("#showIn").checked)
    for (const [did, m] of Object.entries(S.jacksIn))
      for (const [j, fs] of Object.entries(m)) for (const f of fs) c.push({ a: `devin:${did}:${j}`, b: `fin:${f}`, kind: "in", f });
  if ($("#showIns").checked)
    for (const [did, m] of Object.entries(S.slots || {}))
      for (const [s, fs] of Object.entries(m)) for (const f of fs) c.push({ a: `fins:${f}`, b: `slot:${did}:${s}`, kind: "ins", f });
  if ($("#showOut").checked)
    for (const [did, m] of Object.entries(S.jacksOut))
      for (const [j, o] of Object.entries(m)) {
        if (!o.src) continue;
        let a = null;
        if (o.pick === "mix") a = `src:mix:${o.src}`;
        else if (o.pick === "uout") a = o.src > 168 ? `src:uo:${o.src}` : srcToDevIn(o.src);
        if (a) c.push({ a, b: `devout:${did}:${j}`, kind: "out" });
      }
  return c;
}

function prow(title, jacks) {
  const r = el("div", "prow");
  r.append(el("div", "plabel", title));
  const g = el("div", "pjacks");
  for (const [id, num, tip, cls] of jacks) {
    const b = el("button", "pj" + (cls ? " " + cls : ""));
    b.dataset.id = id; b.title = tip;
    b.append(el("i"), el("span", null, num));
    g.append(b);
  }
  r.append(g);
  return r;
}

function renderPatch() {
  if (drag) return;
  const key = JSON.stringify([S.jacksIn, S.jacksOut, S.slots, S.devices, S.faders.map((f) => [f.name, f.routable]),
    $("#showIn").checked, $("#showOut").checked, $("#showIns").checked, tapSel]);
  if (key === patchKey && $("#patch").childElementCount) return;
  patchKey = key;
  const P = $("#patch"); P.innerHTML = "";
  const add = (h) => P.append(el("h2", "phead", h));
  const fnm = (f) => `${f.label}${f.name ? " " + f.name : ""}`;

  add("Inputs");
  for (const d of devs("box").filter((x) => x.inCount)) {
    const j = [];
    for (let i = 1; i <= d.inCount; i++) {
      const L = lbl(d, "in", i);
      j.push([`devin:${d.id}:${i}`, i, `${d.name} In ${i}${L ? " (" + L + ")" : ""} = ${META.portNames[d.inPort]} ${d.inStart + i - 1}`]);
    }
    P.append(prow(`${d.name} In`, j));
  }
  add("Channels");
  const ch = S.faders.filter((f) => f.id.startsWith("ch")), ax = S.faders.filter((f) => f.id.startsWith("aux"));
  P.append(prow("Ch input", ch.map((f) => [`fin:${f.id}`, f.id.slice(2), `${fnm(f)} input · ${f.src}`, f.routable ? "" : "lock"])));
  P.append(prow("Aux input", ax.map((f) => [`fin:${f.id}`, f.id.slice(3), `${fnm(f)} input · ${f.src}`, f.routable ? "" : "lock"])));
  P.append(prow("Ch insert", ch.map((f) => [`fins:${f.id}`, f.id.slice(2), `${fnm(f)} insert${f.ins ? " · " + f.ins : ""}`, "ins"])));
  P.append(prow("Aux insert", ax.map((f) => [`fins:${f.id}`, f.id.slice(3), `${fnm(f)} insert${f.ins ? " · " + f.ins : ""}`, "ins"])));
  const insDevs = devs("insert").filter((x) => x.insCount);
  if (insDevs.length) add("Insert devices");
  for (const d of insDevs) {
    const j = [];
    for (let s = 1; s <= d.insCount; s++) {
      const inf = ((S.slotInfo || {})[d.id] || {})[s] || {};
      const L = lbl(d, "ins", s);
      j.push([`slot:${d.id}:${s}`, s, `${d.name} ${s}${L ? " (" + L + ")" : ""}${inf.fx ? " · " + inf.fx : ""}${inf.warn ? "\n⚠ " + inf.warn : ""}`, "ins" + (inf.warn ? " warn" : "")]);
    }
    P.append(prow(d.name, j));
  }
  add("Output sources");
  P.append(prow("Main/Bus/Mtx", MIX_PATCH().map(([v, t]) => [`src:mix:${v}`, t.replace("Matrix ", "M").replace("Bus ", "B").replace("Main ", "").replace("Mono/C", "C"), `${t} (for Local Out / Aux Out jacks)`, "src"])));
  P.append(prow("Out patch", UO_PATCH().map(([v, t]) => [`src:uo:${v}`, t.replace("Output ", "").replace("P16 ", "P").replace("Aux Out ", "A").replace("Monitor ", "Mon"), `${t} (for AES50 / Card output jacks)`, "src"])));
  const outDevs = devs("box").filter((x) => x.outPort && x.outCount);
  if (outDevs.length) add("Outputs");
  for (const d of outDevs) {
    const o = S.jacksOut[d.id] || {}, j = [];
    for (let i = 1; i <= d.outCount; i++) {
      const L = lbl(d, "out", i), x = o[i] || {};
      j.push([`devout:${d.id}:${i}`, i, `${d.name} Out ${i}${L ? " (" + L + ")" : ""} · ${x.label || "?"}`, "out" + (x.routable ? "" : " lock")]);
    }
    P.append(prow(`${d.name} Out`, j));
  }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("cables"); P.prepend(svg);
  if (tapSel) { const t = P.querySelector(`[data-id="${tapSel}"]`); if (t) t.classList.add("sel"); }
  requestAnimationFrame(drawCables);
}

function jackPos(id) {
  const P = $("#patch"), e = P.querySelector(`.pj[data-id="${id}"] i`);
  if (!e) return null;
  const r = e.getBoundingClientRect(), pr = P.getBoundingClientRect();
  return { x: r.left - pr.left + r.width / 2, y: r.top - pr.top + r.height / 2 };
}

function cablePath(p, q) {
  const sag = 30 + Math.hypot(q.x - p.x, q.y - p.y) * 0.18;
  return `M${p.x},${p.y} C${p.x},${p.y + sag} ${q.x},${q.y + sag} ${q.x},${q.y}`;
}

function drawCables() {
  const P = $("#patch"), svg = P.querySelector("svg.cables");
  if (!svg) return;
  svg.setAttribute("width", P.scrollWidth); svg.setAttribute("height", P.scrollHeight);
  svg.innerHTML = "";
  patchCables().forEach((c, i) => {
    const p = jackPos(c.a), q = jackPos(c.b);
    if (!p || !q) return;
    const col = CABLE_COLORS[(c.f ? S.faders.findIndex((f) => f.id === c.f) : i) % CABLE_COLORS.length];
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", cablePath(p, q));
    path.setAttribute("stroke", col);
    path.classList.add("cable");
    path.onclick = () => unplugCable(c);
    const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
    t.textContent = "Tap to unplug";
    path.append(t);
    svg.append(path);
    P.querySelector(`.pj[data-id="${c.a}"]`)?.classList.add("on");
    P.querySelector(`.pj[data-id="${c.b}"]`)?.classList.add("on");
  });
}
window.addEventListener("resize", () => { if (mode === "patch") drawCables(); });
["#showIn", "#showOut", "#showIns"].forEach((s) => ($(s).onchange = () => renderPatch()));

const jackAt = (x, y) => document.elementsFromPoint(x, y).map((e) => e.closest(".pj")).find(Boolean) || null;
const parseId = (id) => { const [t, a, b] = id.split(":"); return { t, a, b: b !== undefined ? Number(b) : undefined }; };

async function connect(x, y) {
  let A = parseId(x), B = parseId(y);
  const order = ["devin", "src", "fin", "fins", "slot", "devout"];
  if (order.indexOf(A.t) > order.indexOf(B.t)) [A, B] = [B, A];
  try {
    let r;
    if (A.t === "devin" && B.t === "fin") r = await api("/api/route/input", { fader: B.a, device: A.a, jack: A.b });
    else if (A.t === "fins" && B.t === "slot") r = await api("/api/route/insert", { fader: A.a, device: B.a, slot: B.b });
    else if ((A.t === "src" || A.t === "devin") && B.t === "devout") {
      const o = (S.jacksOut[B.a] || {})[B.b] || {};
      if (!o.routable) throw new Error("That output is fixed by its block. Change it on the Blocks tab.");
      let source;
      if (A.t === "src") {
        if ((A.a === "mix") !== (o.pick === "mix"))
          throw new Error(o.pick === "mix" ? "Local Out / Aux Out jacks take Main/Bus/Matrix sources." : "AES50 / Card outputs take the Out patch row (Output 1-16, P16, Aux, Mon) or a stage box input.");
        source = A.b;
      } else {
        if (o.pick === "mix") throw new Error("Local Out / Aux Out jacks can't take an input directly. Use a Main/Bus/Matrix source.");
        const d = S.devices.find((z) => z.id === A.a);
        source = SRC_BASE[d.inPort] + d.inStart + A.b - 1;
      }
      r = await api("/api/route/output", { device: B.a, jack: B.b, source });
    } else throw new Error("Those two jacks can't be patched together.");
    toast(r.msg, r.warn);
  } catch (e) { toast(e.message, true); }
  patchKey = ""; await poll();
}

async function unplugCable(c) {
  try {
    let r;
    if (c.kind === "in") r = await api("/api/route/input", { fader: c.f });
    else if (c.kind === "ins") r = await api("/api/route/insert", { fader: c.f });
    else { const B = parseId(c.b); r = await api("/api/route/output", { device: B.a, jack: B.b, source: 0 }); }
    toast("Unplugged: " + r.msg);
  } catch (e) { toast(e.message, true); }
  patchKey = ""; await poll();
}

async function unplugJack(id) {
  const cs = patchCables().filter((c) => c.a === id || c.b === id);
  if (!cs.length) return;
  if (parseId(id).t === "src") { toast("Drag from the output end to unplug.", true); return; }
  for (const c of cs) await unplugCable(c);
}

$("#patch").addEventListener("pointerdown", (e) => {
  const j = e.target.closest(".pj"); if (!j) return;
  e.preventDefault();
  const P = $("#patch"), svg = P.querySelector("svg.cables"), p = jackPos(j.dataset.id);
  const tmp = document.createElementNS("http://www.w3.org/2000/svg", "path");
  tmp.classList.add("cable", "tmp"); svg.append(tmp);
  drag = { id: j.dataset.id, p, tmp, x0: e.clientX, y0: e.clientY, cx: e.clientX, cy: e.clientY, moved: false };
  // auto-scroll when the cable is dragged near the top / bottom edge
  drag.timer = setInterval(() => {
    if (!drag) return;
    const edge = 70, v = drag.cy < edge ? -(edge - drag.cy) / 3 : drag.cy > innerHeight - edge ? (drag.cy - innerHeight + edge) / 3 : 0;
    if (v) { scrollBy(0, v); dragMove(); }
  }, 16);
  P.setPointerCapture(e.pointerId);
});
function dragMove() {
  const pr = $("#patch").getBoundingClientRect();
  drag.tmp.setAttribute("d", cablePath(drag.p, { x: drag.cx - pr.left, y: drag.cy - pr.top }));
  document.querySelectorAll(".pj.over").forEach((x) => x.classList.remove("over"));
  const t = jackAt(drag.cx, drag.cy);
  if (t && t.dataset.id !== drag.id) t.classList.add("over");
}
$("#patch").addEventListener("pointermove", (e) => {
  if (!drag) return;
  drag.cx = e.clientX; drag.cy = e.clientY;
  if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > 8) drag.moved = true;
  dragMove();
});
$("#patch").addEventListener("pointerup", async (e) => {
  if (!drag) return;
  const d = drag; drag = null; d.tmp.remove(); clearInterval(d.timer);
  const inBay = document.elementsFromPoint(e.clientX, e.clientY).includes($("#patch"));
  document.querySelectorAll(".pj.over").forEach((x) => x.classList.remove("over"));
  const t = jackAt(e.clientX, e.clientY);
  if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) > 8) d.moved = true;
  if (!d.moved) {  // tap-tap patching
    if (tapSel && tapSel !== d.id) { const a = tapSel; tapSel = null; await connect(a, d.id); }
    else { tapSel = tapSel === d.id ? null : d.id; patchKey = ""; renderPatch(); }
    return;
  }
  if (t && t.dataset.id !== d.id) await connect(d.id, t.dataset.id);
  else if (!t && inBay) await unplugJack(d.id);
});
$("#patch").addEventListener("pointercancel", () => { if (drag) { drag.tmp.remove(); clearInterval(drag.timer); drag = null; } });

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
  const used = new Set(devs("box").filter((d) => d.outPort && d.outCount).map((d) => d.outPort));
  for (const port of Object.keys(S.blocks.out)) {
    const det = el("details"); det.open = used.has(port);
    det.append(el("summary", null, META.portNames[port] + " outputs" + (used.has(port) ? "" : " (no device)")));
    const g = el("div", "blockgrid");
    for (const b of S.blocks.out[port]) g.append(blockSelect(b, META.outOpts));
    det.append(g); ob.append(det);
  }
  const p = el("p", "note", "Local Out (XLR 1-16) and Aux Out 1-6 are patched one by one on the Route tab. They need no blocks.");
  ob.append(p);
}

$("#allUin").onclick = async () => {
  const uinStart = META.inOpts.findIndex(([, t]) => t.startsWith("User In"));
  try {
    for (let i = 0; i < 4; i++) await api("/api/block", { path: S.blocks.in[i].path, value: uinStart + i });
    toast("Ch 1-32 now use User In 1-32"); poll();
  } catch (e) { toast(e.message, true); }
};

// ---------- devices ----------
function renderDevices() {
  devDraft = JSON.parse(JSON.stringify(S.devices));
  drawDevices();
}

function drawDevices() {
  const L = $("#devList"); L.innerHTML = "";
  devDraft.forEach((d, i) => {
    d.kind = d.kind || "box";
    const c = el("div", "card dev");
    const head = el("div", "head");
    const nm = el("input"); nm.value = d.name; nm.oninput = () => (d.name = nm.value);
    const del = el("button", "danger", "Remove"); del.onclick = () => { devDraft.splice(i, 1); drawDevices(); };
    head.append(nm, del); c.append(head);
    const kind = sel(["box", "insert"], d, "kind", (k) => k === "box" ? "Stage box / I/O" : "Insert device");
    kind.addEventListener("change", drawDevices);
    c.append(field("Type", kind));
    if (d.kind === "box") {
      c.append(
        field("Input port", sel(META.inPorts, d, "inPort", (p) => `${META.portNames[p]} (${META.portSize[p]})`)),
        field("First input (port #)", num(d, "inStart")),
        field("Input count", num(d, "inCount")),
        field("Output port", sel(["", ...META.outPorts], d, "outPort", (p) => p ? `${META.portNames[p]} (${META.portSize[p]})` : "None")),
        field("First output (port #)", num(d, "outStart")),
        field("Output count", num(d, "outCount")),
        field("Jacks per row", num(d, "cols")),
      );
    } else {
      c.append(
        field("Insert point", sel(Object.keys(META.insTypes), d, "insType", (k) => `${META.insTypes[k].name} (${META.insTypes[k].count})`)),
        field("First slot (Aux # / FX side #)", num(d, "insStart")),
        field("Slot count", num(d, "insCount")),
        field("Slots per row", num(d, "cols")),
      );
      const n = el("p", "note full", d.insType === "AUX"
        ? "Slot = Aux Out (send) + Aux In (return) with the same number. Connect your outboard unit there."
        : "Slots: 1 FX1L, 2 FX1R, 3 FX2L … 8 FX4R. Load an insert effect in that FX slot.");
      c.append(n);
      if (d.insType === "AUX") c.append(ioEditor(d));
    }
    c.append(labelEditor(d));
    L.append(c);
  });
}

function jackOptions(side) {
  // side "out": AES50/Card/Aux outputs; "in": any box input
  const opts = [["", "Local Aux jack (no routing)"]];
  for (const b of devDraft.filter((x) => (x.kind || "box") === "box")) {
    const port = side === "out" ? b.outPort : b.inPort;
    const cnt = side === "out" ? b.outCount : b.inCount;
    if (!port || !cnt || (side === "out" && port === "XLR")) continue;
    for (let j = 1; j <= cnt; j++) {
      const L = lbl(b, side, j);
      opts.push([`${b.id}:${j}`, `${b.name} ${side === "out" ? "Out" : "In"} ${j}${L ? " (" + L + ")" : ""}`]);
    }
  }
  return opts;
}

function ioEditor(d) {
  d.io = d.io || {};
  const det = el("details", "full labels");
  det.open = d._ioOpen !== false; det.ontoggle = () => (d._ioOpen = det.open);
  det.append(el("summary", null, "Outboard wiring (auto-routed on insert)"));
  det.append(el("p", "note", "Where the unit is plugged in. When you insert a slot, the app routes Aux Out (send) to the output jack and the input jack to Aux In (return). Leave blank if the unit uses the X32 rear Aux jacks."));
  const outs = jackOptions("out"), ins = jackOptions("in");
  const g = el("div", "iogrid");
  for (let s = 1; s <= d.insCount; s++) {
    const io = (d.io[s] = d.io[s] || {});
    const n = d.insStart + s - 1;
    const mk = (opts, k) => {
      const x = el("select");
      for (const [v, t] of opts) x.append(new Option(t, v));
      x.value = io[k] || ""; x.onchange = () => (io[k] = x.value);
      return x;
    };
    g.append(el("b", null, `${s} · Aux ${n}${lbl(d, "ins", s) ? " · " + lbl(d, "ins", s) : ""}`),
      field("Send to (unit input)", mk(outs, "send")), field("Return from (unit output)", mk(ins, "ret")));
  }
  det.append(g);
  if (!d.insCount) det.append(el("p", "note", "Set slot count first."));
  return det;
}

function labelEditor(d) {
  d.labels = d.labels || {};
  const det = el("details", "full labels");
  det.open = !!d._open; det.ontoggle = () => (d._open = det.open);
  det.append(el("summary", null, "Port labels (what plugs in where)"));
  const parts = d.kind === "insert" ? [["ins", "Slot", d.insCount]] : [["in", "In", d.inCount], ["out", "Out", d.outCount]];
  for (const [k, name, cnt] of parts) {
    if (!cnt) continue;
    d.labels[k] = d.labels[k] || {};
    const g = el("div", "lblgrid");
    for (let j = 1; j <= cnt; j++) {
      const l = el("label", null, `${name} ${j}`);
      const i = el("input"); i.maxLength = 32; i.value = d.labels[k][j] || "";
      i.oninput = () => { d.labels[k][j] = i.value; };
      l.append(i); g.append(l);
    }
    det.append(g);
  }
  if (det.children.length === 1) det.append(el("p", "note", "Set port counts and save first."));
  return det;
}
function field(t, input) { const l = el("label", null, t); l.append(input); return l; }
function num(d, k) { const i = el("input"); i.type = "number"; i.min = 0; i.value = d[k] ?? 0; i.oninput = () => (d[k] = Number(i.value)); return i; }
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
  try {
    await api("/api/config", { devices: devDraft.map(({ _open, _ioOpen, ...d }) => d) });
    toast("Devices saved"); devDraft = null; await poll();
  }
  catch (e) { toast(e.message, true); }
};

// ---------- printable plan ----------
function table(head, rows) {
  const t = el("table"), tr = el("tr");
  for (const h of head) tr.append(el("th", null, h));
  t.append(tr);
  for (const r of rows) {
    const row = el("tr");
    for (const c of r) row.append(el("td", c === "" || c === "—" ? "dim" : "", c === "" ? "—" : c));
    t.append(row);
  }
  return t;
}

function section(title, sub, tbl) {
  const s = el("section");
  const h = el("h3", null, title);
  s.append(h);
  if (sub) s.append(el("div", "meta", sub));
  s.append(tbl);
  return s;
}

function renderPlan() {
  const P = $("#plan"); P.innerHTML = "";
  const name = $("#showName").value.trim();
  P.append(el("h1", null, name ? `${name} — Routing plan` : "Routing plan"));
  P.append(el("div", "meta", `Printed ${new Date().toLocaleString()} · X32 ${S.x32Ip || "(no IP)"}${S.connected ? "" : " · NOT CONNECTED (values may be old)"}`));

  const fname = (id) => { const f = fader(id); return f ? `${f.label}${f.name ? " " + f.name : ""}` : id; };

  P.append(section("Channel list", "", table(
    ["Fader", "Name", "Source", "Plugged in", "Port", "Via", "Insert"],
    S.faders.map((f) => [f.label, f.name, f.src, f.plug || "", f.srcShort || "", f.via || "", f.ins ? f.ins + (f.insOn ? "" : " (off)") : ""]),
  )));

  for (const d of devs("box")) {
    if (d.inCount) {
      const used = S.jacksIn[d.id] || {};
      const rows = [];
      for (let j = 1; j <= d.inCount; j++)
        rows.push([`In ${j}`, lbl(d, "in", j), `${META.portNames[d.inPort]} ${d.inStart + j - 1}`, (used[j] || []).map(fname).join(", ")]);
      P.append(section(`${d.name} — inputs`, `${META.portNames[d.inPort]} ${d.inStart}-${d.inStart + d.inCount - 1}`,
        table(["Jack", "Plugged in", "Port", "Fader"], rows)));
    }
    if (d.outPort && d.outCount) {
      const outs = S.jacksOut[d.id] || {};
      const rows = [];
      for (let j = 1; j <= d.outCount; j++) {
        const o = outs[j] || {};
        rows.push([`Out ${j}`, lbl(d, "out", j), `${META.portNames[d.outPort]} ${d.outStart + j - 1}`, o.label || "", o.via || ""]);
      }
      P.append(section(`${d.name} — outputs`, `${META.portNames[d.outPort]} ${d.outStart}-${d.outStart + d.outCount - 1}`,
        table(["Jack", "Plugged in", "Port", "Source", "Via"], rows)));
    }
  }
  for (const d of devs("insert")) {
    if (!d.insCount) continue;
    const used = S.slots[d.id] || {};
    const rows = [];
    for (let s = 1; s <= d.insCount; s++) {
      const n = d.insStart + s - 1;
      const point = d.insType === "AUX" ? `Aux ${n} (send Aux Out ${n} / return Aux In ${n})` : `FX${Math.ceil(n / 2)}${n % 2 ? "L" : "R"}`;
      const inf = ((S.slotInfo || {})[d.id] || {})[s] || {};
      const wiring = d.insType === "AUX"
        ? [inf.send && "Send → " + inf.send, inf.ret && "Return ← " + inf.ret].filter(Boolean).join(" / ") || "Rear Aux jacks"
        : (inf.fx || "") + (inf.warn ? " ⚠ not an insert type" : "");
      rows.push([`Slot ${s}`, lbl(d, "ins", s), point, wiring, (used[s] || []).map(fname).join(", ")]);
    }
    P.append(section(`${d.name} — inserts`, META.insTypes[d.insType].name, table(["Slot", "Unit", "Insert point", "Wiring / FX", "On fader"], rows)));
  }
}
try { $("#showName").value = localStorage.getItem("x32r-show") || ""; } catch (e) { /* no storage */ }
$("#showName").oninput = () => { try { localStorage.setItem("x32r-show", $("#showName").value); } catch (e) { /* */ } renderPlan(); };
$("#printPlan").onclick = () => { renderPlan(); window.print(); };

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
