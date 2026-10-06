"use strict";
// DeCFD dashboard. Reads a run folder (history.json + the ledger event log) and polls it
// while the run is in progress. Data layout: data/runs.json, data/<run>/...
// served live by orchestrator_py/serve.py or exported statically by export_demo.py.

const POLL_MS = 2000;
const RUNS_POLL_MS = 5000;
const IX_LABEL = {
  initialize: "Program initialized", register_miner: "Miner registered", create_job: "Job created",
  create_task: "Task posted", submit_result: "Result submitted", verify_ok: "Audit passed", cancel_task: "Task cancelled",
  slash: "FRAUD caught", settle_task: "Reward paid", close_job: "Job closed",
};
const REPORT = [
  ["shapes.png", "Best shape over the generations"],
  ["before_after.png", "Flow: generation-0 winner vs final winner"],
  ["convergence.png", "Search progress"],
  ["genes.png", "Design parameters of the best shape"],
  ["network.png", "Work, audits and miners"],
  ["startup.gif", "Flow starting around the final shape: the starting vortex"],
];

const state = {
  runs: [], run: null, history: null, events: [], participants: {}, cluster: "—", program: null,
  selected: 0, follow: true, onlyAudits: false, timer: null, player: null,
  tlMode: null, tlTimer: null, tl: null, userPicked: false,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(d));
const short = (pk) => (pk ? `${pk.slice(0, 4)}…${pk.slice(-4)}` : "—");
const posix = (p) => (p || "").replace(/\\/g, "/");
const base = () => `data/${encodeURIComponent(state.run)}/`;
const onChain = () => state.cluster && state.cluster !== "mock" && state.cluster !== "—";
const explorer = (kind, id) => `https://explorer.solana.com/${kind}/${id}?cluster=${state.cluster}`;

async function getJSON(url) {
  const r = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
async function getText(url) {
  const r = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.text();
}
function parseJSONL(text) {
  const out = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch { /* a line still being written */ }
  }
  return out;
}

// ------------------------------------------------------------------ derived data
function nameOf(pk) {
  const p = state.participants[pk];
  return p ? p.name : short(pk);
}

function minerStats() {
  const miners = new Map();
  for (const e of state.events) {
    if (e.ix === "register_miner") {
      miners.set(e.signer, { pubkey: e.signer, name: e.name, stake0: e.stake, stake: e.stake, earned: 0, tasks: 0, caught: 0, banned: false });
    } else if (e.ix === "submit_result" && miners.has(e.signer)) {
      miners.get(e.signer).tasks += 1;
    } else if (e.ix === "settle_task" && miners.has(e.miner)) {
      miners.get(e.miner).earned += e.paid;
    } else if (e.ix === "slash" && miners.has(e.miner)) {
      const m = miners.get(e.miner);
      m.stake -= e.penalty; m.caught += 1; m.banned = m.banned || e.banned;
    }
  }
  return [...miners.values()];
}

function totals() {
  const t = { tasks: 0, audits: 0, fraud: 0, paid: 0, slashed: 0, toVerifier: 0, budget: null, refund: null, minStake: null };
  for (const e of state.events) {
    if (e.ix === "create_task") t.tasks += 1;
    else if (e.ix === "verify_ok") t.audits += 1;
    else if (e.ix === "slash") { t.audits += 1; t.fraud += 1; t.slashed += e.penalty; t.toVerifier += e.to_verifier; }
    else if (e.ix === "settle_task") t.paid += e.paid;
    else if (e.ix === "create_job") t.budget = e.budget;
    else if (e.ix === "close_job") t.refund = e.refund;
    else if (e.ix === "initialize") t.minStake = e.min_stake;
  }
  return t;
}

// ------------------------------------------------------------------ rendering
function render() {
  renderStatus();
  renderKPIs();
  renderEvolution();
  renderTimeline();
  renderMiners();
  renderEvents();
  renderReport();
}

function renderStatus() {
  const h = state.history;
  const n = h.generations.length;
  const planned = h.config.gen;
  const el = $("status");
  if (h.finished) {
    const mins = h.elapsed ? ` in ${Math.round(h.elapsed / 60)} min` : "";
    el.textContent = `finished${mins}`;
    el.className = "pill";
  } else {
    el.textContent = `running · ${n} of ${planned} generations done`;
    el.className = "pill pill-live";
  }
  $("cluster").innerHTML = onChain() && state.program
    ? `<a href="${explorer("address", state.program)}" target="_blank" rel="noopener">ledger: Solana ${esc(state.cluster)} ↗</a>`
    : `ledger: ${esc(state.cluster)}`;
  $("footer-ledger").innerHTML = onChain() && state.program
    ? `ledger: Solana ${esc(state.cluster)} program <a href="${explorer("address", state.program)}" target="_blank" rel="noopener">${short(state.program)}</a>`
    : "ledger: local mock of the Solana program";
}

function kpi(label, value, sub, alert) {
  return `<div class="kpi${alert ? " alert" : ""}"><div class="label">${esc(label)}</div>` +
    `<div class="value">${value}</div><div class="sub">${sub || "&nbsp;"}</div></div>`;
}

function renderKPIs() {
  const gens = state.history.generations;
  const t = totals();
  if (!gens.length) { $("kpis").innerHTML = kpi("Best lift / drag", "—", "waiting for generation 0"); return; }
  const best = gens[gens.length - 1].best.ld;
  const first = gens[0].best.ld;
  const gain = first > 0 ? `+${Math.round(100 * (best / first - 1))}% vs generation 0` : "";
  const v = state.history.verification;
  const banned = minerStats().filter((m) => m.banned).map((m) => m.name);
  $("kpis").innerHTML = [
    kpi("Best lift / drag", fmt(best), gain),
    kpi("Generations", `${gens.length}<span class="sub"> / ${state.history.config.gen}</span>`,
      v ? `30k-step check: ${fmt(v.ld)}` : `${state.history.config.pop} shapes each`),
    kpi("Tasks computed", t.tasks, `by ${minerStats().length} miners`),
    kpi("Audits", t.audits, t.tasks ? `${Math.round((100 * t.audits) / t.tasks)}% of tasks re-computed` : ""),
    kpi("Fakes caught", t.fraud, banned.length ? `banned: ${esc(banned.join(", "))}`
      : t.fraud ? "stake slashed, nobody banned yet" : "no fraud detected", t.fraud > 0),
    kpi("Paid to miners", t.paid, t.budget != null ? `of a ${t.budget} budget` : ""),
  ].join("");
}

function renderEvolution() {
  const gens = state.history.generations;
  const slider = $("gen-slider");
  slider.max = Math.max(0, gens.length - 1);
  if (state.follow) state.selected = Math.max(0, gens.length - 1);
  state.selected = Math.min(state.selected, Math.max(0, gens.length - 1));
  slider.value = state.selected;
  $("gen-label").textContent = `gen ${state.selected}`;
  const img = $("frame");
  img.style.visibility = gens.length ? "visible" : "hidden";
  if (!gens.length) { $("gen-caption").textContent = "Waiting for the first generation…"; drawChart(); return; }

  const g = gens[state.selected];
  const src = base() + posix(g.frame);
  if (img.getAttribute("src") !== src) img.src = src;
  const b = g.best;
  const by = b.corrected
    ? `<span class="badge badge-fraud">fake by ${esc(b.miner)} corrected by the verifier</span>`
    : `computed by ${esc(b.miner || "—")} <span class="badge badge-ok">verified</span>`;
  $("gen-caption").innerHTML =
    `<b>Generation ${g.gen}</b> · L/D <b>${fmt(b.ld)}</b> (Cd ${fmt(b.cd, 3)}, Cl ${fmt(b.cl, 3)}) · ` +
    `α ${fmt(b.alpha, 1)}°, camber ${fmt(b.camber, 1)}, thickness ${b.t_pts.map((t) => fmt(t, 1)).join(" / ")} · ${by}`;
  drawChart();
}

function drawChart() {
  const gens = state.history.generations;
  const W = 520, H = 300, m = { l: 40, r: 12, t: 10, b: 30 };
  const N = Math.max(state.history.config.gen, gens.length, 2);
  const values = gens.flatMap((g) => g.population_ld.filter((v) => v > 0).concat([g.best.ld]));
  const yMax = Math.max(0.5, ...values) * 1.1;
  const x = (i) => m.l + ((W - m.l - m.r) * i) / (N - 1);
  const y = (v) => H - m.b - ((H - m.t - m.b) * v) / yMax;
  const parts = [];
  const step = yMax > 4 ? 1 : yMax > 2 ? 0.5 : 0.25;
  for (let v = 0; v <= yMax; v += step) {
    parts.push(`<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`);
    parts.push(`<text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(step < 1 ? 2 : 0)}</text>`);
  }
  const xStep = Math.max(1, Math.ceil(N / 10));
  for (let i = 0; i < N; i += xStep) parts.push(`<text x="${x(i)}" y="${H - m.b + 16}" text-anchor="middle">${i}</text>`);
  parts.push(`<text x="${(m.l + W - m.r) / 2}" y="${H - 2}" text-anchor="middle">generation</text>`);
  const axis = `<g class="axis">${parts.join("")}</g>`;

  const dots = gens.flatMap((g) => g.population_ld.filter((v) => v > 0)
    .map((v) => `<circle class="pop" cx="${x(g.gen)}" cy="${y(v)}" r="2.6"/>`)).join("");
  const line = gens.length ? `<path class="best" d="${gens.map((g, i) => `${i ? "L" : "M"}${x(g.gen)},${y(g.best.ld)}`).join("")}"/>` : "";
  const pts = gens.map((g) => `<circle class="best-pt" cx="${x(g.gen)}" cy="${y(g.best.ld)}" r="${g.gen === state.selected ? 5.5 : 3.5}"/>`).join("");
  const sel = gens.length ? `<line class="sel" x1="${x(state.selected)}" x2="${x(state.selected)}" y1="${m.t}" y2="${H - m.b}"/>` : "";
  const colW = (W - m.l - m.r) / (N - 1);
  const hits = gens.map((g) => `<rect class="hit" data-gen="${g.gen}" x="${x(g.gen) - colW / 2}" y="${m.t}" width="${colW}" height="${H - m.t - m.b}"/>`).join("");
  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Best lift over drag by generation">${axis}${sel}${dots}${line}${pts}${hits}</svg>`;
}

// ------------------------------------------------------------------ timeline
// Built from the wall-clock time `t` of each ledger event (runs recorded before it had none).
function timelineData() {
  const tasks = new Map();
  const marks = [];          // the verifier's audits
  const gens = new Map();    // epoch -> time its first task was posted
  const rows = [];           // miner pubkeys in registration order
  const banned = new Map();  // pubkey -> time of the ban
  const audits = [];         // the verifier's re-computations: from the event before a batch to its verdicts
  let t0 = null, last = null;
  for (const e of state.events) {
    if (e.t == null) continue;
    if (t0 == null) t0 = e.t;
    const prev = last ?? e.t;
    last = e.t;
    if (e.ix === "verify_ok" || e.ix === "slash") {
      const b = audits[audits.length - 1];
      if (b && b.end === prev && e.t - prev < 0.3) b.end = e.t;
      else audits.push({ start: prev, end: e.t });
    }
    const k = e.task ? tasks.get(e.task) : null;
    if (e.ix === "register_miner") {
      if (!rows.includes(e.signer)) rows.push(e.signer);
    } else if (e.ix === "create_task") {
      tasks.set(e.task, { id: e.task, epoch: e.epoch, miner: e.assigned, start: e.t, end: null, state: "open" });
      if (!gens.has(e.epoch)) gens.set(e.epoch, e.t);
    } else if (!k) {
      continue;
    } else if (e.ix === "submit_result") {
      Object.assign(k, { end: e.t, state: "done", miner: k.miner || e.signer, sig: e.sig });
    } else if (e.ix === "cancel_task") {
      Object.assign(k, { end: e.t, state: "cancelled" });
    } else if (e.ix === "verify_ok" || e.ix === "slash") {
      k.audit = e.ix === "slash" ? "fraud" : "ok";
      k.auditT = e.t;
      marks.push({ t: e.t, task: k });
      if (e.banned) banned.set(e.miner, e.t);
    } else if (e.ix === "settle_task") {
      k.paid = e.paid;
    }
  }
  for (const k of tasks.values()) if (k.miner && !rows.includes(k.miner)) rows.push(k.miner);
  return { tasks, marks, gens, rows, banned, audits, t0, last };
}

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const secs = (s) => (s < 90 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);

function renderTimeline() {
  const sec = $("timeline-section");
  const d = timelineData();
  if (d.t0 == null || !d.tasks.size) { sec.hidden = true; return; }
  sec.hidden = false;
  const running = !state.history.finished;
  const now = running ? Math.max(d.last, Date.now() / 1000) : d.last;
  const epochs = [...d.gens.keys()].sort((a, b) => a - b);
  const mode = state.tlMode || (running && epochs.length > 3 ? "recent" : "all");
  $("tl-recent").setAttribute("aria-pressed", mode === "recent");
  $("tl-all").setAttribute("aria-pressed", mode === "all");
  // The clock starts at the first task: waiting for remote nodes to join can take minutes
  const start = d.gens.get(epochs[0]);
  const from = (mode === "recent" && epochs.length > 3 ? d.gens.get(epochs[epochs.length - 3]) : start) - 1;
  const to = Math.max(now, from + 20) + (running ? 2 : 0);

  // Overlapping tasks of one miner (parallel threads, or a queue on a remote node) get lanes
  const visible = [...d.tasks.values()].filter((k) => k.miner && (k.end ?? now) >= from)
    .sort((a, b) => a.start - b.start);
  const laneOf = new Map(), laneEnds = new Map();
  for (const k of visible) {
    const ends = laneEnds.get(k.miner) || [];
    let i = ends.findIndex((e) => e <= k.start);
    if (i < 0) { i = ends.length; ends.push(0); }
    ends[i] = k.end ?? Infinity;
    laneEnds.set(k.miner, ends);
    laneOf.set(k.id, i);
  }
  const rows = [...d.rows, "verifier"];
  const geo = new Map();   // pk -> {y, h, barH, pitch, top}
  const TOP = 20, BOT = 22, R = 10;
  let y = TOP;
  for (const pk of rows) {
    const n = Math.max(1, (laneEnds.get(pk) || []).length);
    const barH = n === 1 ? 12 : n <= 3 ? 8 : 5, pitch = barH + (n <= 3 ? 3 : 2);
    const h = Math.max(34, n * pitch + 12);
    geo.set(pk, { y, h, barH, pitch, top: y + (h - (n * pitch - (pitch - barH))) / 2 });
    y += h;
  }
  const H = y + BOT;

  const el = $("timeline");
  const W = Math.max(320, el.clientWidth || 900);
  const LBL = W < 600 ? 96 : 150;
  const x = (t) => LBL + ((W - LBL - R) * (t - from)) / (to - from);
  const out = [];

  out.push(`<defs>${["local", "remote"].map((c) =>
    `<pattern id="tl-hatch-${c}" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">` +
    `<rect width="2.5" height="6" class="hatch-${c}"/></pattern>`).join("")}` +
    `<clipPath id="tl-clip"><rect x="${LBL}" y="0" width="${W - LBL}" height="${H}"/></clipPath></defs>`);
  out.push(`<g>${rows.map((pk) => `<rect class="tl-row" x="0" y="${geo.get(pk).y}" width="${W}" height="${geo.get(pk).h}"/>`).join("")}</g>`);

  // Time axis, from the first task
  const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const step = STEPS.find((s) => ((to - from) / s) * 64 <= W - LBL) || 7200;
  for (let s = Math.ceil((from - start) / step) * step; start + s <= to; s += step) {
    const xx = x(start + s);
    out.push(`<line class="tl-tick" x1="${xx}" x2="${xx}" y1="${TOP}" y2="${H - BOT}"/>`);
    out.push(`<text x="${xx}" y="${H - 6}" text-anchor="middle">${clock(s)}</text>`);
  }
  // Generation boundaries
  let lastLabel = -1e9;
  for (const ep of epochs) {
    const t = d.gens.get(ep);
    if (t < from) continue;
    const xx = x(t);
    out.push(`<line class="tl-gen" x1="${xx}" x2="${xx}" y1="${TOP - 4}" y2="${H - BOT}"/>`);
    if (xx - lastLabel > 44) {
      out.push(`<text class="tl-gen-label" x="${xx + 3}" y="${TOP - 6}">gen ${ep}</text>`);
      lastLabel = xx;
    }
  }

  const busy = new Set();
  const bars = [];
  for (const k of visible) {
    const g = geo.get(k.miner);
    if (!g) continue;
    if (k.state === "open") busy.add(k.miner);
    const remote = (state.participants[k.miner] || {}).remote;
    const cls = ["tl-bar", remote ? "remote" : "local", k.state === "open" ? "open" : "",
      k.state === "cancelled" ? "cancelled" : "", k.audit === "fraud" ? "fraud" : ""].join(" ");
    const x1 = x(Math.max(k.start, from)), x2 = Math.max(x(k.end ?? now), x1 + 2);
    const by = g.top + laneOf.get(k.id) * g.pitch;
    bars.push(`<rect class="${cls}" data-task="${esc(k.id)}" x="${x1}" y="${by}" width="${x2 - x1}" height="${g.barH}" rx="2"/>`);
    if (k.audit === "ok") bars.push(`<circle class="tl-ok" cx="${x2}" cy="${by + g.barH / 2}" r="${Math.min(3.5, g.barH / 2 + 1)}"/>`);
  }
  // Banned miners: their row goes red from the moment of the ban
  for (const [pk, t] of d.banned) {
    const g = geo.get(pk);
    if (!g) continue;
    const x1 = x(Math.max(t, from));
    out.push(`<rect class="tl-banned" x="${x1}" y="${g.y + 3}" width="${Math.max(0, W - R - x1)}" height="${g.h - 6}"/>`);
    out.push(`<text class="tl-banned-label" x="${x1 + 6}" y="${g.y + g.h / 2 + 4}">banned</text>`);
  }
  // The verifier: grey while it re-computes a batch of samples, then a green dot per matching
  // hash or a red cross per caught fake
  const vg = geo.get("verifier"), vy = vg.y + vg.h / 2;
  for (const b of d.audits) {
    if (b.end < from) continue;
    const x1 = x(Math.max(b.start, from));
    bars.push(`<rect class="tl-audit" x="${x1}" y="${vy - 5}" width="${Math.max(2, x(b.end) - x1)}" height="10" rx="2"/>`);
  }
  for (const m of d.marks) {
    if (m.t < from) continue;
    const xx = x(m.t);
    bars.push(m.task.audit === "fraud"
      ? `<g class="tl-mark" data-task="${esc(m.task.id)}"><rect x="${xx - 7}" y="${vy - 7}" width="14" height="14" fill="transparent"/>` +
        `<path class="tl-x" d="M${xx - 5},${vy - 5}L${xx + 5},${vy + 5}M${xx - 5},${vy + 5}L${xx + 5},${vy - 5}"/></g>`
      : `<circle class="tl-ok tl-mark" data-task="${esc(m.task.id)}" cx="${xx}" cy="${vy}" r="4.5"/>`);
  }
  out.push(`<g clip-path="url(#tl-clip)">${bars.join("")}</g>`);
  if (running) {
    const xn = x(now);
    out.push(`<line class="tl-now" x1="${xn}" x2="${xn}" y1="${TOP - 4}" y2="${H - BOT}"/>`);
    out.push(`<text class="tl-now-label" x="${xn}" y="${TOP - 6}" text-anchor="end">now</text>`);
  }

  // Row labels: name, then what the miner is doing (live) or what it is
  for (const pk of rows) {
    const g = geo.get(pk);
    const p = state.participants[pk] || {};
    const name = pk === "verifier" ? "verifier" : nameOf(pk);
    let sub, cls = "";
    if (pk === "verifier") sub = "re-computes samples";
    else if (d.banned.has(pk)) { sub = "banned"; cls = "banned"; }
    else if (running && busy.has(pk)) { sub = "● computing"; cls = "busy"; }
    else sub = running ? "idle" : p.remote ? "remote node" : "local miner";
    const mid = g.y + g.h / 2;
    out.push(`<text class="tl-name" x="8" y="${mid - 2}">${esc(name)}</text>`);
    out.push(`<text class="tl-sub ${cls}" x="8" y="${mid + 11}">${esc(sub)}</text>`);
  }

  state.tl = d;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Tasks per miner over time">${out.join("")}</svg>`;
}

function timelineTip(id) {
  const d = state.tl;
  const k = d && d.tasks.get(id);
  if (!k) return "";
  const p = state.participants[k.miner] || {};
  const running = !state.history.finished;
  const dur = k.end != null ? k.end - k.start : Math.max(Date.now() / 1000, d.last) - k.start;
  const what = k.state === "open" ? `computing for ${secs(dur)}…`
    : k.state === "cancelled" ? `no answer after ${secs(dur)}: cancelled and recomputed elsewhere`
    : `${secs(dur)} from posting to result`;
  const audit = k.audit === "ok" ? "audit: re-computed, hashes match"
    : k.audit === "fraud" ? "<b>fake result caught</b>: stake slashed"
    : k.state === "done" ? "not sampled for an audit" : "";
  const paid = k.audit === "fraud" ? "no reward" : k.paid != null ? `reward paid: ${k.paid}`
    : k.state === "done" && running ? "reward: after the challenge window" : "";
  return [`<b>${esc(k.id)}</b> · generation ${k.epoch}`,
    `${esc(nameOf(k.miner))} (${p.remote ? "remote node" : "local miner"})`, what, audit, paid]
    .filter(Boolean).join("<br>");
}

function renderMiners() {
  const miners = minerStats();
  const t = totals();
  $("miners").querySelector("tbody").innerHTML = miners.map((mn) => {
    const p = state.participants[mn.pubkey] || {};
    const frac = mn.stake0 ? Math.max(0, mn.stake / mn.stake0) : 0;
    const low = mn.banned || (t.minStake != null && mn.stake < t.minStake);
    return `<tr>
      <td><div class="miner-name">${esc(mn.name)}</div>${onChain()
        ? `<a class="pubkey" href="${explorer("address", mn.pubkey)}" target="_blank" rel="noopener" title="Wallet on Solana Explorer">${short(mn.pubkey)} ↗</a>`
        : `<div class="pubkey">${short(mn.pubkey)}</div>`}
        ${p.honest === false ? '<div class="tag">simulated cheater</div>' : ""}
        ${p.remote ? '<div class="tag">remote node</div>' : ""}</td>
      <td><span class="status ${mn.banned ? "status-banned" : "status-active"}">${mn.banned ? "banned" : "active"}</span></td>
      <td class="num">${mn.tasks}</td>
      <td class="num">${mn.earned}</td>
      <td><div class="stake"><div class="bar"><i class="${low ? "low" : ""}" style="width:${(100 * frac).toFixed(0)}%"></i></div>
        <span class="mono">${mn.stake}</span></div></td>
      <td class="num">${mn.caught}</td></tr>`;
  }).join("");

  const escrow = t.budget == null ? null : t.refund != null ? 0 : t.budget - t.paid;
  const cell = (label, value) => `<div><div class="label">${label}</div><div class="value">${value ?? "—"}</div></div>`;
  $("economy").innerHTML = [
    cell("Job budget", t.budget),
    cell("Paid to miners", t.paid),
    cell(t.refund != null ? "Refunded to client" : "Still in escrow", t.refund != null ? t.refund : escrow),
    cell("Stake slashed", t.slashed),
    cell("→ verifier reward", t.toVerifier),
    cell("→ treasury", t.slashed - t.toVerifier),
  ].join("");
}

function describe(e) {
  const task = e.task ? `<span class="mono">${esc(e.task)}</span>` : "";
  switch (e.ix) {
    case "initialize": return `verifier ${esc(nameOf(e.verifier))}, min stake ${e.min_stake}, slash ${e.slash_bps / 100}%`;
    case "register_miner": return `${esc(e.name)} staked ${e.stake}`;
    case "create_job": return `budget ${e.budget}, reward ${e.reward} per task, binary ${esc(e.binary_hash.slice(0, 10))}…`;
    case "create_task": return `${task} (epoch ${e.epoch})`;
    case "submit_result": return `${task} by ${esc(nameOf(e.signer))}${e.remote ? " (signed on the node itself)" : ""}, ` +
      `commitment ${esc(e.result_hash.slice(0, 10))}…`;
    case "verify_ok": return `${task} re-computed, hashes match`;
    case "slash": return `${task} by ${esc(nameOf(e.miner))}: hash mismatch, slashed ${e.penalty} (${e.to_verifier} to the verifier)${e.banned ? " — miner banned" : ""}`;
    case "settle_task": return `${esc(nameOf(e.miner))} +${e.paid} for ${task}`;
    case "close_job": return `${e.refund} returned to the client`;
    case "cancel_task": return `${task} withdrawn: its miner did not answer`;
    default: return "";
  }
}

function renderEvents() {
  const list = state.onlyAudits ? state.events.filter((e) => e.ix === "verify_ok" || e.ix === "slash") : state.events;
  const rows = list.slice(-300).reverse().map((e) => {
    const cls = e.ix === "slash" ? "fraud" : e.ix === "verify_ok" ? "audit" : "";
    const tx = onChain() && e.sig ? ` <a class="tx" href="${explorer("tx", e.sig)}" target="_blank" rel="noopener">tx ↗</a>` : "";
    const when = e.t != null ? ` title="${new Date(e.t * 1000).toLocaleTimeString()}"` : "";
    return `<li class="${cls}"><span class="slot"${when}>#${e.slot}</span><span><span class="ix">${esc(IX_LABEL[e.ix] || e.ix)}</span> ` +
      `<span class="detail">${describe(e)}</span>${tx}</span></li>`;
  });
  $("events").innerHTML = rows.join("") || "<li><span></span><span class='detail'>No events yet.</span></li>";
}

function renderReport() {
  const sec = $("report-section");
  if (!state.history.finished) { sec.hidden = true; return; }
  sec.hidden = false;
  const key = state.run;
  if ($("report").dataset.run === key) return;
  $("report").dataset.run = key;
  $("report").innerHTML = REPORT.map(([file, title]) =>
    `<figure><figcaption>${esc(title)}</figcaption><img loading="lazy" src="${base()}report/${file}" alt="${esc(title)}" onerror="this.parentElement.remove()"></figure>`).join("");
}

// ------------------------------------------------------------------ data loading
async function refresh() {
  clearTimeout(state.timer);
  const run = state.run;
  try {
    const [history, log] = await Promise.all([getJSON(base() + "history.json"), getText(base() + "network/ledger_tx.jsonl").catch(() => "")]);
    if (run !== state.run) return;   // the user switched runs meanwhile
    state.history = history;
    state.events = parseJSONL(log);
    if (!Object.keys(state.participants).length) {
      try {
        const p = await getJSON(base() + "network/participants.json");
        state.participants = p.participants;
        state.cluster = p.cluster;
        state.program = p.program_id;
      } catch { /* written at the start of a run */ }
    }
    render();
  } catch (err) {
    $("status").textContent = "cannot load run";
    console.error(err);
  }
  const live = state.history && !state.history.finished;
  if (live) state.timer = setTimeout(refresh, POLL_MS);
  // Between data polls the timeline keeps moving: open tasks grow until their result arrives
  if (live && !state.tlTimer) state.tlTimer = setInterval(renderTimeline, 1000);
  if (!live && state.tlTimer) { clearInterval(state.tlTimer); state.tlTimer = null; }
}

async function selectRun(name) {
  state.run = name;
  state.participants = {};
  state.tlMode = null;
  $("run-select").value = name;
  state.follow = true;
  $("follow").checked = true;
  history.replaceState(null, "", `#run=${encodeURIComponent(name)}`);
  await refresh();
}

function fillRunSelect() {
  const sel = $("run-select");
  sel.innerHTML = state.runs.map((r) =>
    `<option value="${esc(r.name)}">${esc(r.label || r.name)}${r.finished ? "" : " (running)"}</option>`).join("");
  if (state.run) sel.value = state.run;
}

// The run list is re-read every few seconds: a run started while the page is open shows up by
// itself and is opened, unless the viewer has picked a run by hand
async function pollRuns() {
  try {
    const runs = await getJSON("data/runs.json");
    const key = (list) => JSON.stringify(list.map((r) => [r.name, r.finished]));
    const known = new Set(state.runs.map((r) => r.name));
    const started = runs.find((r) => !known.has(r.name) && !r.finished);
    const changed = key(runs) !== key(state.runs);
    state.runs = runs;
    if (changed) fillRunSelect();
    if (started && (!state.userPicked || !state.run)) await selectRun(started.name);
  } catch { /* the server is restarting */ }
  setTimeout(pollRuns, RUNS_POLL_MS);
}

async function init() {
  $("run-select").addEventListener("change", (e) => { state.userPicked = true; selectRun(e.target.value); });
  try {
    state.runs = await getJSON("data/runs.json");
  } catch {
    state.runs = [];
  }
  const fromHash = decodeURIComponent((location.hash.match(/run=([^&]+)/) || [])[1] || "");
  // A run in progress wins over a remembered link: watching it is what the local dashboard is for
  const newest = state.runs[0];
  const first = newest && !newest.finished ? newest.name
    : state.runs.some((r) => r.name === fromHash) ? fromHash : newest && newest.name;
  if (first) {
    state.run = first;
    fillRunSelect();
    await selectRun(first);
  } else {
    $("status").textContent = "no runs yet: start orchestrator_py/app.py";
  }
  setTimeout(pollRuns, RUNS_POLL_MS);
}

// ------------------------------------------------------------------ interaction
function setGeneration(g, userAction) {
  if (userAction) { state.follow = false; $("follow").checked = false; }
  state.selected = g;
  renderEvolution();
}

$("gen-slider").addEventListener("input", (e) => setGeneration(Number(e.target.value), true));
$("follow").addEventListener("change", (e) => { state.follow = e.target.checked; renderEvolution(); });
$("only-audits").addEventListener("change", (e) => { state.onlyAudits = e.target.checked; renderEvents(); });
$("play").addEventListener("click", () => {
  const btn = $("play");
  if (state.player) { clearInterval(state.player); state.player = null; btn.textContent = "▶"; return; }
  const last = state.history.generations.length - 1;
  if (state.selected >= last) setGeneration(0, true);
  btn.textContent = "❚❚";
  state.player = setInterval(() => {
    const end = state.history.generations.length - 1;
    if (state.selected >= end) { clearInterval(state.player); state.player = null; btn.textContent = "▶"; return; }
    setGeneration(state.selected + 1, true);
  }, 600);
});

const tip = $("tooltip");
$("chart").addEventListener("mousemove", (e) => {
  const r = e.target.closest(".hit");
  if (!r) { tip.hidden = true; return; }
  const g = state.history.generations[Number(r.dataset.gen)];
  const pop = g.population_ld.filter((v) => v > 0).sort((a, b) => a - b);
  const median = pop.length ? pop[Math.floor(pop.length / 2)] : null;
  tip.innerHTML = `Generation <b>${g.gen}</b><br>best L/D <b>${fmt(g.best.ld)}</b><br>population median <b>${fmt(median)}</b><br>by ${esc(g.best.miner || "—")}`;
  tip.hidden = false;
  tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 190)}px`;
  tip.style.top = `${e.clientY + 14}px`;
});
$("chart").addEventListener("mouseleave", () => { tip.hidden = true; });
$("chart").addEventListener("click", (e) => {
  const r = e.target.closest(".hit");
  if (r) setGeneration(Number(r.dataset.gen), true);
});

$("timeline").addEventListener("mousemove", (e) => {
  const r = e.target.closest("[data-task]");
  const html = r ? timelineTip(r.dataset.task) : "";
  if (!html) { tip.hidden = true; return; }
  tip.innerHTML = html;
  tip.hidden = false;
  tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 260)}px`;
  tip.style.top = `${e.clientY + 14}px`;
});
$("timeline").addEventListener("mouseleave", () => { tip.hidden = true; });
$("timeline").addEventListener("click", (e) => {
  const r = e.target.closest("[data-task]");
  const k = r && state.tl && state.tl.tasks.get(r.dataset.task);
  const gens = state.history.generations;
  if (k && k.epoch < gens.length) setGeneration(k.epoch, true);
});
$("tl-recent").addEventListener("click", () => { state.tlMode = "recent"; renderTimeline(); });
$("tl-all").addEventListener("click", () => { state.tlMode = "all"; renderTimeline(); });
let resizeFrame = 0;
window.addEventListener("resize", () => {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => { if (state.history) renderTimeline(); });
});

init();
