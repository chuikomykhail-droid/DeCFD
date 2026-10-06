"use strict";
// DeCFD dashboard. Reads a run folder (history.json + the ledger event log) and polls it
// while the run is in progress. Data layout: data/runs.json, data/<run>/...
// served live by orchestrator_py/serve.py or exported statically by export_demo.py.

const POLL_MS = 2000;
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
    kpi("Fakes caught", t.fraud, banned.length ? `banned: ${esc(banned.join(", "))}` : "no fraud detected", t.fraud > 0),
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
  if (!gens.length) { $("gen-caption").textContent = "Waiting for the first generation…"; drawChart(); return; }

  const g = gens[state.selected];
  const img = $("frame");
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

function renderMiners() {
  const miners = minerStats();
  const t = totals();
  $("miners").querySelector("tbody").innerHTML = miners.map((mn) => {
    const p = state.participants[mn.pubkey] || {};
    const frac = mn.stake0 ? Math.max(0, mn.stake / mn.stake0) : 0;
    const low = mn.banned || (t.minStake != null && mn.stake < t.minStake);
    return `<tr>
      <td><div class="miner-name">${esc(mn.name)}</div><div class="pubkey">${short(mn.pubkey)}</div>
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
    case "submit_result": return `${task} by ${esc(nameOf(e.signer))}, commitment ${esc(e.result_hash.slice(0, 10))}…`;
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
    return `<li class="${cls}"><span class="slot">#${e.slot}</span><span><span class="ix">${esc(IX_LABEL[e.ix] || e.ix)}</span> ` +
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
  if (state.history && !state.history.finished) state.timer = setTimeout(refresh, POLL_MS);
}

async function selectRun(name) {
  state.run = name;
  state.participants = {};
  state.follow = true;
  $("follow").checked = true;
  history.replaceState(null, "", `#run=${encodeURIComponent(name)}`);
  await refresh();
}

async function init() {
  try {
    state.runs = await getJSON("data/runs.json");
  } catch {
    $("status").textContent = "no runs found";
    return;
  }
  const sel = $("run-select");
  sel.innerHTML = state.runs.map((r) =>
    `<option value="${esc(r.name)}">${esc(r.label || r.name)}${r.finished ? "" : " (running)"}</option>`).join("");
  const fromHash = decodeURIComponent((location.hash.match(/run=([^&]+)/) || [])[1] || "");
  const first = state.runs.some((r) => r.name === fromHash) ? fromHash : state.runs[0] && state.runs[0].name;
  if (!first) { $("status").textContent = "no runs found"; return; }
  sel.value = first;
  sel.addEventListener("change", () => selectRun(sel.value));
  await selectRun(first);
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

init();
