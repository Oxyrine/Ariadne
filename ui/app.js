"use strict";
/* Ariadne UI. Every number on screen comes from the running system through /api; nothing is precomputed.
   All dynamic text passes through esc() before it is placed in markup. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (p) => "₹" + Math.round(p / 100).toLocaleString("en-IN");
const inrShort = (p) => { const r = p / 100; return r >= 1e7 ? `₹${(r / 1e7).toFixed(2)} cr` : r >= 1e5 ? `₹${(r / 1e5).toFixed(2)} lakh` : inr(p); };
const shortHash = (h, n = 10) => (h ? h.slice(0, n + 2) + "…" + h.slice(-4) : "");
const pct = (bps) => (bps / 100).toFixed(1) + "%";
const fmtTime = (ts) => new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
const fmtDate = (d) => new Date(d + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

const IC = {
  check: '<path d="M3 8.5l3.2 3L13 4.5"/>', x: '<path d="M4 4l8 8M12 4l-8 8"/>', plus: '<path d="M8 3v10M3 8h10"/>',
  arrow: '<path d="M3 8h10M9 4l4 4-4 4"/>', swap: '<path d="M3 5h10M10 2l3 3-3 3M13 11H3M6 8l-3 3 3 3"/>',
  clock: '<circle cx="8" cy="8" r="6"/><path d="M8 5v3.5l2 1.5"/>', doc: '<path d="M4 2h6l3 3v9H4zM9.5 2v3.5H13"/>',
  minus: '<path d="M3 8h10"/>',
};
const icon = (n, size) => `<svg viewBox="0 0 16 16" ${size ? `width="${size}" height="${size}"` : ""} fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${IC[n]}</svg>`;

async function api(path, body) {
  const r = await fetch(path, body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const S = { status: null, pool: null, units: null, evidence: null, verify: null, label: null, results: {}, seed: "A", mode: "console" };
let token = 0, poll = null, ROOT_EL = null;
const PAGES = {};  // later scripts register pages here
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
const NEEDS_POOL = new Set(["pool", "journey", "attacks", "life", "evidence", "units", "report"]);
(function theme() {
  const t = store.get("ariadne-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = t;
})();
S.mode = store.get("ariadne-mode") || "console";

const CHECKS = {
  RULES_PINNED: "Rules match the signed, pinned hash",
  GROUP_MAP_PINNED: "Buyer-group map matches its pinned hash",
  EVIDENCE_PINNED: "Evidence set matches its pinned hash",
  COMMITMENT_REPLAY: "Membership history replays to the on-chain commitment",
  OWNERSHIP: "The originator owned every unit when it entered",
  RECEIVABLE_UNIQUE: "No receivable was live in two pools",
  UNIT_RULES: "Every unit passed the unit rules on entry",
  POOL_RULES: "Pool rules held at every manifest version",
  ATTESTATIONS: "Every attestation matches the verifier's own verdict",
  PERFORMANCE: "Pool performance (reported, not pass or fail)",
};
const FEATURES = { value_symmetry: "Value symmetry", timing: "Timing", entity_age: "Entity age", tightness: "Loop tightness", shared_attributes: "Shared attributes", round_amounts: "Round amounts" };
const WHY = {
  NotOwner: "Only the current owner of a unit can put it in a pool. This unit was re-discounted onward, so the contract refused.",
  DuplicateReceivable: "The fingerprint of this invoice is already registered. Formatting differences do not create a second receivable.",
  AlreadyEncumbered: "That receivable is already live in another pool, so it cannot be pooled again.",
};
const ATTACKS = [
  { kind: "stale-owner", title: "Pool a unit you no longer own", desc: "Financier B re-discounted a unit to C. B now tries to put it into a new pool anyway." },
  { kind: "duplicate", title: "Register the same invoice twice", desc: "A second platform registers the hero invoice again, with different capitalisation and spacing." },
  { kind: "double-pool", title: "Put one receivable in two pools", desc: "B offers a unit that is already live in the first pool to a second pool." },
];
const ROUTES = [["pool", "Pool"], ["journey", "Journey"], ["attacks", "Attacks"], ["life", "Life"], ["build", "Build"], ["evidence", "Evidence"],
  ["units", "Units"], ["compiler", "Compiler"], ["metrics", "Metrics"], ["context", "Context"], ["qa", "Q&A"], ["report", "Report"]];

function ruleText(r) {
  const v = r.value;
  return { state_required: `Unit must be ${v} when pooled`, tenor_max_days: `Invoice tenor at most ${v} days`,
    min_days_to_maturity: `At least ${v} days left when pooled`, evidence_score_max: `Evidence score at most ${v}, unless a reviewer clears it`,
    buyer_group_concentration_max_bps: `No resolved buyer group above ${pct(v)} of the pool` }[r.kind] || `${r.kind}: ${v}`;
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; t.setAttribute("role", "status");
  document.body.appendChild(t); setTimeout(() => t.remove(), 1800);
}

async function busy(btn, fn) {
  btn.setAttribute("aria-busy", "true"); btn.disabled = true;
  try { return await fn(); } finally { btn.removeAttribute("aria-busy"); btn.disabled = false; }
}

const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.05 });
function reveal() {
  $$(".reveal").forEach((el) => {
    const sibs = [...el.parentElement.children].filter((c) => c.classList.contains("reveal"));
    el.style.setProperty("--i", Math.min(sibs.indexOf(el), 8));
    io.observe(el);
  });
}
const setMain = (html) => { (ROOT_EL || $("#main")).innerHTML = html; reveal(); };
const failing = (v) => (v && v.status !== "VERIFIED" ? v.failed : []);

/* ---------------------------------------------------------------- header */

const SUN = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/></svg>';
const MOON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.8 5.8 0 1 0 7.1 7.1z"/></svg>';

function renderHeader() {
  const st = S.status, ready = st && st.phase === "ready", hash = location.hash.replace("#/", "");
  const cur = hash.split("/")[0] || (S.mode === "story" ? "story" : "pool");
  document.body.classList.toggle("story", S.mode === "story");
  const nav = $("#nav");
  nav.hidden = !ready || S.mode === "story";
  nav.innerHTML = ready ? ROUTES.map(([r, l]) => `<a href="#/${r}" ${cur === r ? 'aria-current="page"' : ""}>${l}</a>`).join("") : "";
  const dark = document.documentElement.dataset.theme === "dark";
  let right = `<div class="seg-ctl" role="group" aria-label="Interface mode"><button aria-pressed="${S.mode === "story"}" data-mode="story">Story</button><button aria-pressed="${S.mode === "console"}" data-mode="console">Console</button></div>` +
    `<button class="icon-btn" id="theme" aria-label="Switch to ${dark ? "light" : "dark"} theme" title="Theme">${dark ? SUN : MOON}</button>` +
    '<span class="tag yellow synth">Synthetic data</span>';
  if (ready) {
    const v = S.verify;
    const vt = v ? (v.status === "VERIFIED" ? '<span class="tag green">Verified</span>' : '<span class="tag red">Not verified</span>') : '<span class="tag">Not checked</span>';
    right = (S.snap ? "" : `<a href="#/pool" style="text-decoration:none">${vt}</a><span class="chainmeta">block ${st.block} · local chain</span>`) + right +
      (S.snap ? "" : '<button class="btn ghost small" id="reset">Reset</button>');
  }
  $("#topright").innerHTML = right;
  $$("[data-mode]").forEach((b) => b.onclick = () => {
    S.mode = b.dataset.mode; store.set("ariadne-mode", S.mode);
    location.hash = S.mode === "story" ? "#/story/0" : "#/pool";
    route();
  });
  $("#theme").onclick = () => {
    const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t; store.set("ariadne-theme", t); renderHeader();
  };
  const bar = $("#snapbar");
  bar.hidden = !S.snap;
  if (S.snap) bar.innerHTML = `Recorded from a live run of seed ${esc(S.snap.meta.seed)} at ${esc(S.snap.meta.time)}, commit <code>${esc(S.snap.meta.commit)}</code>. Actions replay the recorded result; run it locally to fire them for real.`;
  const rs = $("#reset");
  if (rs) rs.onclick = async () => {
    if (!confirm("Stop the local chain and clear this session?")) return;
    await api("/api/reset", {});
    S.pool = S.units = S.evidence = S.verify = S.metrics = null; S.results = {}; S.label = null;
    await refresh();
  };
}

/* --------------------------------------------------------------- landing */

function landing() {
  const err = S.status.error ? `<div class="err-box" role="alert">Startup failed: ${esc(S.status.error)}</div>` : "";
  setMain(`
  <section class="hero reveal">
    <span class="eyebrow">Pool integrity for TReDS receivables</span>
    <h1>Who owns this invoice right now?</h1>
    <p class="lead">A small supplier's invoice can now be bought, resold and bundled for investors. Ariadne lets a trustee trace every invoice in the bundle and check, without trusting the seller, that the bundle is still what it was sold as.</p>
    ${err}
    <div class="start">
      <label class="f">Dataset
        <select id="seed">
          <option value="A">Seed A: demo, every scenario</option>
          <option value="B">Seed B: unseen, same generator</option>
          <option value="C">Seed C: adversarial, expect misses</option>
        </select>
      </label>
      <button class="btn" id="go">Start with a pool already built ${icon("arrow")}</button>
      <button class="btn ghost" id="go2">Start and build the pool myself</button>
    </div>
    <p class="tl-m" style="margin-top:14px">Starts a local blockchain, deploys the contract and replays about 190 synthetic factoring units. It takes one to two minutes.</p>
  </section>
  <section class="notes grid g3">
    <div class="card reveal"><h3>The contract prevents</h3><p>Pooling a receivable you no longer own, registering the same invoice twice, and putting one receivable in two pools. Each is refused on-chain.</p></div>
    <div class="card reveal"><h3>The verifier detects</h3><p>A trustee recomputes every eligibility attestation from chain events and the signed rules. A wrong attestation is flagged with the unit, the rule and the block.</p></div>
    <div class="card reveal"><h3>Ariadne does not claim</h3><p>That invoices reflect real goods, that it detects fraud, or that it replaces CERSAI or MonetaGo. Loop and group findings are evidence for a human.</p></div>
  </section>`);
  $("#seed").value = S.seed;
  const go = (auto, nextHash) => async (e) => {
    S.seed = $("#seed").value;
    await busy(e.currentTarget, async () => { S.status = await api("/api/start", { seed: S.seed, auto }); if (nextHash) location.hash = nextHash; startPolling(); route(); });
  };
  $("#go").onclick = go(true);
  $("#go2").onclick = go(false, "#/build");
}

function progress() {
  const st = S.status;
  const items = st.steps.map((name, i) => {
    const cls = i < st.step ? "done" : i === st.step ? "now" : "";
    return `<li class="${cls}"><span class="pip">${i < st.step ? icon("check") : ""}</span>${esc(name)}<span class="d">${i === st.step ? esc(st.detail) : ""}</span></li>`;
  }).join("");
  const log = st.log.join("\n") || "Waiting for output...";
  if ($(".steps")) { $(".steps").innerHTML = items; $("pre.log").textContent = log; return; }
  setMain(`
  <section class="hero"><span class="eyebrow">Starting seed ${esc(st.seed)}</span><h1>Building the ledger</h1>
  <p class="lead">Every step below runs for real against a local chain.</p></section>
  <div class="card" style="max-width:640px"><ul class="steps">${items}</ul>
  <details><summary>Setup log</summary><pre class="log">${esc(log)}</pre></details></div>`);
}

/* ------------------------------------------------------------------ pool */

async function pagePool() {
  const my = token;
  const [pool, verify] = await Promise.all([api("/api/pool"), S.verify ? Promise.resolve(S.verify) : api("/api/verify", {})]);
  if (my !== token) return;
  S.pool = pool; S.verify = verify; renderHeader();
  const ok = verify.status === "VERIFIED", bad = failing(verify);
  const scale = Math.max(1500, (pool.groups[0]?.bps || 0) * 1.15);
  const bars = pool.groups.map((g) => {
    const over = g.bps > pool.capBps, label = g.merged ? `${g.name} · ${g.memberNames.length} entities` : g.name;
    return `<div class="barrow"><span>${esc(label)}${g.merged ? ' <span class="tag yellow">Resolved group</span>' : ""}</span>
      <div class="track"><div class="fill ${over ? "over" : g.merged ? "merged" : ""}" style="width:${(g.bps / scale) * 100}%"></div><div class="cap" style="left:${(pool.capBps / scale) * 100}%"></div></div>
      <span class="num mono">${pct(g.bps)}</span></div>`;
  }).join("");
  const rows = Object.entries(verify.checks).map(([k, c]) => {
    const cls = c.status === "PASS" ? "pass" : c.status === "FAIL" ? "fail" : "info";
    return `<tr><td><div class="status-cell"><span class="check-ic ${cls}">${icon(cls === "pass" ? "check" : cls === "fail" ? "x" : "minus")}</span>${esc(CHECKS[k] || k)}</div></td>
      <td><code>${k}</code></td><td>${esc(c.detail)}</td></tr>`;
  }).join("");
  const flags = verify.failures.map((f) => `<div class="flag"><strong>${esc(f.check)}</strong> · unit <code>${esc(f.unit)}</code>${f.rule !== "-" ? ` · ${esc(f.rule)} <code>${esc(f.kind)}</code>` : ""}<br>${esc(f.detail)} · block ${f.block}</div>`).join("");
  const recent = [...verify.timeline].reverse().slice(0, 12).map((t) => `<tr><td class="num mono">${t.block}</td><td>${esc(t.op.replaceAll("_", " ").toLowerCase())}</td><td><a href="#/journey" data-unit="${esc(t.unit)}">${esc(t.unit)}</a></td><td class="num">v${t.version}</td></tr>`).join("");
  const p = verify.performance;
  setMain(`
  <div class="page-head reveal"><span class="eyebrow">${esc(pool.name)}</span><h1>Is this pool still what was sold?</h1>
  <p>Recomputed from chain events, the signed rule set and the frozen buyer-group map. Nothing the originator reports is used.</p></div>
  <section><div class="verdict ${ok ? "ok" : "bad"} reveal" role="status">
    <div><h2>${icon(ok ? "check" : "x", 26)}${ok ? "Verified" : "Not verified"}</h2>
    <p>${ok ? `All ${Object.values(verify.checks).filter((c) => c.status === "PASS").length} checks pass at manifest v${verify.version}.` : `These checks failed: ${esc(bad.join(", "))}. The pool no longer matches what was promised.`}</p></div>
    <div class="actions"><button class="btn ghost" id="reverify">Run verification again</button><a class="btn" href="/api/report" target="_blank" rel="noopener">Open trustee report</a></div></div></section>
  <section class="grid bento">
    <div class="card stat reveal"><div class="k">Live members</div><div class="v">${pool.members}</div><div class="s">${p.settled} settled · ${p.defaulted} defaulted since sealing</div></div>
    <div class="card stat reveal"><div class="k">${gloss("manifest", "Manifest version")}</div><div class="v">v${pool.manifest}</div><div class="s">${pool.sealed ? "Sealed" : "Open"} · originator ${esc(pool.originator)}</div></div>
    <div class="card stat reveal"><div class="k">Outstanding</div><div class="v">${inrShort(pool.totalPaise)}</div><div class="s">Face value of live members</div></div>
    <div class="card reveal wide"><div class="k tl-m">${gloss("commitment", "Membership commitment")}, computed inside the contract</div>
      <p class="hash" style="margin:10px 0 12px">${esc(pool.commitment)}</p>
      <button class="btn ghost small" id="copy">Copy</button></div>
    <div class="card reveal wide"><div class="tl-m" style="margin-bottom:10px">Signed rules pinned to this pool</div>
      ${pool.rules.map((r) => `<div class="row" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)"><span>${esc(ruleText(r))}</span><code>${esc(r.id)}</code></div>`).join("")}</div>
  </section>
  <section class="reveal"><div class="sec-head"><div><h2>Buyer concentration</h2><p>The cap applies to each resolved group, not each GSTIN. A group hidden behind several identifiers is measured as one. Dashed line: ${pct(pool.capBps)} cap. ${pool.rulesSource === "compiled" ? "These rules were compiled from the prospectus and signed by a reviewer." : ""}</p></div></div>
    <div class="card"><div class="bars">${bars}</div></div></section>
  ${pool.sellers && pool.sellers.length ? `<section class="reveal"><div class="sec-head"><div><h2>Seller concentration</h2><p>No single seller above ${pct(pool.sellerCapBps || 0)}.</p></div></div>
    <div class="card"><div class="bars">${pool.sellers.map((g) => `<div class="barrow"><span>${esc(g.name)}</span><div class="track"><div class="fill ${g.bps > pool.sellerCapBps ? "over" : ""}" style="width:${(g.bps / Math.max(700, pool.sellerCapBps * 1.4)) * 100}%"></div><div class="cap" style="left:${(pool.sellerCapBps / Math.max(700, pool.sellerCapBps * 1.4)) * 100}%"></div></div><span class="num mono">${pct(g.bps)}</span></div>`).join("")}</div></div></section>` : ""}
  <section class="reveal"><div class="sec-head"><div><h2>Checks</h2><p>Historical checks use state at the block where each event happened, rebuilt from events.</p></div></div>
    <div class="card tablewrap"><table class="tbl"><thead><tr><th>Check</th><th>Id</th><th>Detail</th></tr></thead><tbody>${rows}</tbody></table>
    ${flags ? `<div class="flags"><h3>Flagged</h3>${flags}</div>` : ""}</div></section>
  <section class="reveal"><div class="sec-head"><div><h2>Recent membership events</h2><p>${verify.events} events replayed. Newest first.</p></div></div>
    <div class="card tablewrap"><table class="tbl"><thead><tr><th class="num">Block</th><th>Event</th><th>Unit</th><th class="num">Manifest</th></tr></thead><tbody>${recent}</tbody></table></div></section>`);
  $("#reverify").onclick = (e) => busy(e.currentTarget, async () => { S.verify = await api("/api/verify", {}); route(); });
  $("#copy").onclick = () => navigator.clipboard.writeText(pool.commitment).then(() => toast("Commitment copied"));
  $$("a[data-unit]").forEach((a) => a.addEventListener("click", () => { S.label = a.dataset.unit; }));
}

/* --------------------------------------------------------------- journey */

const EVT = {
  UnitRegistered: (e) => ({ t: `Registered on platform ${e.registrar}`, m: "The platform recorded the unit and its fingerprint.", tone: "blue", i: "doc" }),
  UnitFinanced: (e) => ({ t: `Financed by financier ${e.financier}`, m: "The financier paid the supplier and became the owner.", tone: "green", i: "check" }),
  UnitTransferred: (e) => ({ t: `Re-discounted from ${e.from} to ${e.to}`, m: `Transfer number ${e.transferCount}. Ownership moved on-chain.`, tone: "blue", i: "swap" }),
  UnitSettled: () => ({ t: "Settled by the platform", m: "The buyer paid. The unit is finished.", tone: "green", i: "check" }),
  UnitDefaulted: () => ({ t: "Marked defaulted by the platform", m: "Past its due date and unpaid.", tone: "red", i: "x" }),
  MembershipChanged: (e) => ({
    ADD: { t: `Added to ${e.pool}`, m: "The contract confirmed ownership, no other live pool, and a valid attestation.", tone: "green", i: "plus" },
    REMOVE: { t: `Removed from ${e.pool} before sealing`, m: "", tone: "yellow", i: "minus" },
    SUBSTITUTE_IN: { t: `Swapped into ${e.pool}`, m: `Manifest v${e.manifestVersion}.`, tone: "green", i: "swap" },
    SUBSTITUTE_OUT: { t: `Swapped out of ${e.pool}`, m: `Manifest v${e.manifestVersion}.`, tone: "yellow", i: "swap" },
    SETTLED: { t: `Left ${e.pool}: settled`, m: "", tone: "green", i: "minus" },
    DEFAULTED: { t: `Left ${e.pool}: defaulted`, m: "", tone: "red", i: "minus" },
  }[e.op]),
};

async function pageJourney() {
  const my = token;
  if (!S.units) S.units = await api("/api/units");
  const label = S.label || S.status.heroLabel;
  let data, err = "";
  try { data = await api("/api/trace?unit=" + encodeURIComponent(label)); } catch (e) { err = e.message; }
  if (my !== token) return;
  S.label = label;
  const opts = S.units.map((u) => `<option value="${esc(u.label)}">`).join("");
  const head = `<div class="page-head reveal"><span class="eyebrow">One invoice, start to now</span><h1>Every step is an event anyone can read.</h1>
    <p>Pick any factoring unit. The story below is read straight from the chain.</p></div>
    <form class="pick reveal" id="pick"><label class="f">Unit<input type="text" id="unit" list="units" value="${esc(label)}" autocomplete="off" spellcheck="false"></label>
    <datalist id="units">${opts}</datalist><button class="btn ghost">Show journey</button>
    <button type="button" class="btn ghost" id="hero">Hero invoice</button></form>`;
  if (err) { setMain(head + `<div class="err-box" role="alert">${esc(err.includes("StopIteration") ? "No unit with that label." : err)}</div>`); bindPick(); return; }
  const u = data.unit;
  const tl = data.events.map((e) => { const x = EVT[e.event](e); return `<li class="reveal"><span class="dot ${x.tone}">${icon(x.i)}</span><div class="tl-t">${esc(x.t)}</div><div class="tl-m">${esc(x.m)} <span class="mono">block ${e.block} · ${fmtTime(e.ts)}</span></div></li>`; }).join("");
  setMain(head + `
  <section class="grid" style="grid-template-columns:1fr 1.3fr;align-items:start">
    <div class="card reveal"><div class="tl-m">Current owner</div><div class="owner-big">${u.owner ? "Financier " + esc(u.owner) : "None yet"}</div>
      <div class="row" style="margin:14px 0 22px"><span class="tag ${u.state === "POOLED" ? "green" : u.state === "SETTLED" ? "blue" : u.state === "DEFAULTED" ? "red" : ""}">${esc(u.state)}</span>${u.pool ? `<span class="tag">${esc(u.pool)}</span>` : ""}${u.review === "EXCLUDED" ? '<span class="tag red">Excluded by reviewer</span>' : ""}</div>
      <dl class="kv"><dt>Unit</dt><dd class="mono">${esc(u.label)}</dd><dt>Seller</dt><dd>${esc(u.seller)}</dd><dt>Buyer</dt><dd>${esc(u.buyer)}${u.group ? ` <span class="tag yellow">${esc(u.group)}</span>` : ""}</dd>
      <dt>Face value</dt><dd>${inr(u.amountPaise)}</dd><dt>Invoice date</dt><dd>${fmtDate(u.invoiceDate)}</dd><dt>Due</dt><dd>${fmtDate(u.dueDate)}</dd>
      <dt>Re-discounts</dt><dd>${u.transfers}</dd><dt>Evidence score</dt><dd>${u.score}</dd><dt>Unit id</dt><dd class="hash">${esc(shortHash(data.unitId, 16))}</dd></dl></div>
    <div class="card reveal"><ul class="timeline">${tl}</ul></div>
  </section>`);
  bindPick();
}
function bindPick() {
  $("#pick").onsubmit = (e) => { e.preventDefault(); S.label = $("#unit").value.trim(); route(); };
  $("#hero").onclick = () => { S.label = S.status.heroLabel; route(); };
}

/* --------------------------------------------------------------- attacks */

function attackResult(kind) {
  const r = S.results[kind]; if (!r) return "";
  const x = r.result || {};
  if (!x.error) return `<div class="err"><span class="tag red">Not rejected</span></div><p>The contract accepted this. That should not happen.</p>`;
  return `<div class="err"><span class="tag ${r.ok ? "green" : "red"}">${r.ok ? "Rejected" : "Unexpected"}</span><code>${esc(x.error)}</code></div>
    <p>${esc(WHY[x.error] || "")}</p>
    <ul><li>${icon("check")}Refused by the contract on-chain, not by this page.</li>
    <li>${icon("check")}${x.unchanged ? `Chain state unchanged: block ${esc(x.blockBefore)} before and after, same owners, states and pools.` : "State changed. Investigate."}</li></ul>
    <details><summary>Raw log</summary><pre class="log">${esc((r.log || []).join("\n"))}</pre></details>`;
}

function pageAttacks() {
  const cards = ATTACKS.map((a) => `<div class="card act reveal"><h3>${esc(a.title)}</h3><p class="desc">${esc(a.desc)}</p>
    <div><button class="btn" data-attack="${a.kind}">Fire this attack</button></div>
    <div class="result" id="res-${a.kind}" aria-live="polite" ${S.results[a.kind] ? "" : "hidden"}>${attackResult(a.kind)}</div></div>`).join("");
  setMain(`<div class="page-head reveal"><span class="eyebrow">Attacks</span><h1>Three ways to break a pool. None of them works.</h1>
    <p>Each button sends a real transaction to the contract. A rejected transaction moves nothing, and the page shows the proof.</p></div>
    <section class="grid g3">${cards}</section>`);
  $$("[data-attack]").forEach((b) => b.onclick = () => busy(b, async () => {
    const k = b.dataset.attack;
    try { S.results[k] = await api("/api/attack", { kind: k }); } catch (e) { S.results[k] = { ok: false, result: { error: e.message }, log: [] }; }
    const el = $("#res-" + k); el.hidden = false; el.innerHTML = attackResult(k); refreshHeader();
  }));
}

/* ------------------------------------------------------------- pool life */

const verifyTag = (v) => v ? `<span class="tag ${v.status === "VERIFIED" ? "green" : "red"}">${v.status === "VERIFIED" ? "Still verified" : "Not verified"}</span>` : "";
const LIFE = [
  { kind: "settle", n: "1", title: "Settle a batch of units", desc: "Buyers pay. The platform marks units settled and they leave the live pool. Choices keep the largest buyer group under 9.5%.", btn: "Settle up to 20 units" },
  { kind: "substitute", n: "2", title: "Substitute a unit", desc: "The originator swaps a live unit for a spare. The engine checks the pool after the swap and signs only if every rule holds.", btn: "Substitute one unit" },
  { kind: "default", n: "3", title: "A unit defaults", desc: "The chain clock moves to just past the earliest due date and the platform marks that unit defaulted.", btn: "Mark one default" },
];
function lifeResult(kind) {
  const r = S.results[kind]; if (!r) return "";
  const x = r.result || {};
  if (!r.ok) return `<p>No clean candidate was found for this step.${r.log && r.log.length ? "" : ""}</p>`;
  const txt = { settle: `Settled ${esc(x.settled)} units. ${esc(x.live)} live members remain and the largest buyer group is ${pct(x.largestBps || 0)}.`,
    substitute: `Swapped <code>${esc(x.out)}</code> for <code>${esc(x.into)}</code>. Manifest is now v${esc(x.version)}, and every rule passes after the swap.`,
    default: `<code>${esc(x.unit)}</code> passed its due date and platform ${esc(x.registrar)} marked it defaulted.` }[kind];
  return `<p>${txt}</p><div class="row">${verifyTag(r.verify)}<a href="#/pool" class="tl-m">See the full checks</a></div>`;
}
function swapRefusal() {
  const r = S.results["swap-refused"]; if (!r) return "";
  const x = r.result || {};
  if (!x.fails) return "<p>No rule-breaking swap candidate was found.</p>";
  return `<div class="err"><span class="tag red">Engine refuses</span><code>${esc(x.out)} → ${esc(x.into)}</code></div>
    <ul>${x.fails.map((f) => `<li>${icon("x")}<span><code>${esc(f.rule)}</code> ${esc(f.detail)}</span></li>`).join("")}</ul>`;
}
function swapForced() {
  const r = S.results["swap-forced"]; if (!r) return "";
  const v = r.verify;
  if (!(r.result || {}).accepted) return "<p>No rule-breaking swap candidate was found, so nothing was forced.</p>";
  return `<div class="err"><span class="tag red">Contract accepted it</span><span class="tag ${v && v.status === "VERIFIED" ? "green" : "red"}">${v && v.status === "VERIFIED" ? "Verified" : "Not verified"}</span></div>
    <p>The contract cannot judge eligibility, so a forged attestation gets through. The verifier re-ran every rule and caught it.</p>
    <ul>${(v ? v.failures : []).slice(0, 3).map((f) => `<li>${icon("x")}<span><code>${esc(f.check)}</code> unit <code>${esc(f.unit)}</code> ${esc(f.rule)} · ${esc(f.detail)} · block ${esc(f.block)}</span></li>`).join("")}</ul>
    <p style="margin-top:12px"><a href="#/pool">Open the pool checks</a></p>`;
}

function pageLife() {
  const cards = LIFE.map((a) => `<div class="card act reveal"><span class="steplabel">Step ${a.n}</span><h3>${esc(a.title)}</h3><p class="desc">${esc(a.desc)}</p>
    <div><button class="btn" data-life="${a.kind}">${esc(a.btn)}</button></div>
    <div class="result" id="life-${a.kind}" aria-live="polite" ${S.results[a.kind] ? "" : "hidden"}>${lifeResult(a.kind)}</div></div>`).join("");
  setMain(`<div class="page-head reveal"><span class="eyebrow">After issuance</span><h1>A pool keeps its promises as it changes.</h1>
    <p>Run the steps in order. After each one the verifier replays the whole history again, so you can see the pool stay verified.</p></div>
    <section class="grid g3">${cards}</section>
    <section class="reveal"><div class="sec-head"><div><h2>A swap that breaks the cap</h2><p>The honest engine refuses. Then we simulate a stolen engine key, which signs anyway. This changes the pool for the rest of the session; Reset clears it.</p></div></div>
      <div class="split"><div class="card act"><span class="steplabel">Step 1</span><h3>Ask the engine to attest the swap</h3>
        <p class="desc">Swap a small unit for a large one from the same buyer group, pushing the group past its cap.</p>
        <div><button class="btn" id="refuse">Ask the engine</button></div><div class="result" id="res-refuse" ${S.results["swap-refused"] ? "" : "hidden"}>${swapRefusal()}</div></div>
      <div class="card act"><span class="steplabel">Step 2</span><h3>Force it with a compromised key</h3>
        <p class="desc">The engine key signs the bad swap directly. The contract accepts it. Watch the verifier.</p>
        <div><button class="btn danger" id="force">Force the swap</button></div><div class="result" id="res-force" ${S.results["swap-forced"] ? "" : "hidden"}>${swapForced()}</div></div></div></section>`);
  const run = (b, kind, out, render) => busy(b, async () => {
    try { S.results[kind] = await api("/api/action", { kind }); } catch (e) { S.results[kind] = { ok: false, result: {}, log: [e.message] }; }
    if (S.results[kind].verify) S.verify = S.results[kind].verify;
    refreshHeader();
    const el = $(out); el.hidden = false; el.innerHTML = render();
  });
  $$("[data-life]").forEach((b) => b.onclick = () => run(b, b.dataset.life, "#life-" + b.dataset.life, () => lifeResult(b.dataset.life)));
  $("#refuse").onclick = (e) => run(e.currentTarget, "swap-refused", "#res-refuse", swapRefusal);
  let armed = 0;
  $("#force").onclick = (e) => {
    const b = e.currentTarget;
    if (!armed) { armed = 1; b.textContent = "Confirm: break this pool"; b.classList.add("armed"); setTimeout(() => { armed = 0; b.textContent = "Force the swap"; b.classList.remove("armed"); }, 5000); return; }
    armed = 0; b.classList.remove("armed"); b.textContent = "Force the swap";
    run(b, "swap-forced", "#res-force", swapForced);
  };
}

/* -------------------------------------------------------------- evidence */

async function pageEvidence() {
  const my = token;
  const ev = S.evidence || (S.evidence = await api("/api/evidence"));
  if (my !== token) return;
  const seg = Object.keys(FEATURES);
  const cyc = ev.cycles.map((c) => {
    const held = c.score > ev.cutoff;
    const bar = seg.map((k, i) => `<span class="seg${i}" style="width:${(c.features[k] || 0) / 10}%" title="${FEATURES[k]} ${c.features[k] || 0}"></span>`).join("");
    const legend = seg.map((k, i) => `<span><i class="seg${i}"></i>${FEATURES[k]} ${c.features[k] || 0}</span>`).join("");
    const chips = c.units.map((u) => `<span class="chip">${esc(u.label)}${u.review === "EXCLUDED" ? " · excluded" : u.review === "CLEARED" ? " · cleared" : ""}</span>`).join("");
    return `<div class="card reveal"><div class="scorehead"><div><span class="tag ${held ? "red" : "green"}">${held ? "Held for review" : "Below the cut-off, kept"}</span>
      <h3 style="margin-top:12px">${esc(c.id)}: loop of ${c.entities.length} entities</h3><p class="entity-list" style="margin:6px 0 0">${c.entities.map(esc).join(" → ")}</p></div><div class="big-score">${c.score}</div></div>
      <div class="scorebar" role="img" aria-label="Score ${c.score} of 1000, cut-off ${ev.cutoff}">${bar}<div class="cap" style="left:${ev.cutoff / 10}%"></div></div>
      <div class="caplabel" style="margin-top:6px;text-align:right">cut-off ${ev.cutoff}</div>
      <div class="legend">${legend}</div>
      <div style="margin-top:18px"><div class="tl-m" style="margin-bottom:8px">${c.units.length} units in this loop</div><div class="chips">${chips}</div></div></div>`;
  }).join("");
  const parseLink = (l) => { const [k, v, m] = l.split(":"); return `${k === "same_pan" ? "Same PAN" : "Shared director"} <code>${esc(v)}</code> across ${m.split(",").length} GSTINs`; };
  const grp = ev.groups.map((g) => `<div class="card reveal"><span class="tag yellow">Resolved group</span><h3 style="margin:12px 0 6px">${esc(g.id)}: ${g.members.length} entities, one economic group</h3>
    <p class="entity-list">${g.members.map(esc).join(", ")}</p><ul style="padding:0;list-style:none;margin:12px 0 0;color:var(--muted);font-size:14px">${g.links.map((l) => `<li>${parseLink(l)}</li>`).join("")}</ul></div>`).join("");
  setMain(`<div class="page-head reveal"><span class="eyebrow">Evidence, not verdicts</span><h1>Finding loops is easy. Telling real ones from fake ones is the hard part.</h1>
    <p>Every loop gets a score with its working shown. The weights are hand-set, not trained, and a person decides what happens to each held unit.</p></div>
    <section><div class="sec-head reveal"><div><h2>Trading loops</h2><p>Bars show how the score is made up. Loops above the cut-off are held for a reviewer. In this run the reviewer excluded them.</p></div></div>
    <div class="grid" style="grid-template-columns:1fr 1fr;align-items:start">${cyc || '<p class="empty">No trading loops found in this dataset.</p>'}</div></section>
    <section><div class="sec-head reveal"><div><h2>Buyer groups</h2><p>Only strong signals merge entities automatically: the same PAN under two GSTINs, or a shared director. The concentration cap applies to the group.</p></div></div>
    <div class="grid" style="grid-template-columns:1fr 1fr;align-items:start">${grp || '<p class="empty">No entities were merged on strong signals in this dataset.</p>'}</div></section>`);
}

/* ----------------------------------------------------------------- units */

async function pageUnits() {
  const my = token;
  if (!S.units) S.units = await api("/api/units");
  if (my !== token) return;
  const states = [...new Set(S.units.map((u) => u.state))].sort();
  setMain(`<div class="page-head reveal"><span class="eyebrow">Ledger</span><h1>${S.units.length} factoring units</h1><p>Read from the contract right now. Select a unit to see its journey.</p></div>
    <div class="row reveal" style="margin-bottom:20px"><input type="search" id="q" placeholder="Search unit, seller or buyer" aria-label="Search units" style="min-width:260px">
      <select id="fs" aria-label="Filter by state"><option value="">All states</option>${states.map((s) => `<option>${s}</option>`).join("")}</select>
      <select id="fo" aria-label="Filter by owner"><option value="">Any owner</option><option>A</option><option>B</option><option>C</option></select><span class="tl-m" id="count"></span></div>
    <div class="card tablewrap reveal" style="padding:8px"><table class="tbl hover"><thead><tr><th>Unit</th><th>Seller → buyer</th><th class="num">Face value</th><th>Due</th><th>Owner</th><th>State</th><th class="num">Evidence</th></tr></thead><tbody id="tb"></tbody></table></div>`);
  const draw = () => {
    const q = $("#q").value.toLowerCase(), fs = $("#fs").value, fo = $("#fo").value;
    const rows = S.units.filter((u) => (!q || (u.label + u.seller + u.buyer).toLowerCase().includes(q)) && (!fs || u.state === fs) && (!fo || u.owner === fo));
    $("#count").textContent = `${rows.length} shown`;
    $("#tb").innerHTML = rows.map((u) => `<tr data-l="${esc(u.label)}" tabindex="0"><td class="mono">${esc(u.label)}</td><td>${esc(u.seller)} → ${esc(u.buyer)}${u.group ? ` <span class="tag yellow">${esc(u.group)}</span>` : ""}</td>
      <td class="num">${inr(u.amountPaise)}</td><td>${fmtDate(u.dueDate)}</td><td>${u.owner ? esc(u.owner) : "-"}</td>
      <td><span class="tag ${u.state === "POOLED" ? "green" : u.state === "DEFAULTED" ? "red" : u.state === "SETTLED" ? "blue" : ""}">${esc(u.state)}</span></td>
      <td class="num">${u.review === "EXCLUDED" ? '<span class="tag red">Excluded</span>' : esc(u.score)}</td></tr>`).join("");
    $$("#tb tr").forEach((tr) => { const go = () => { S.label = tr.dataset.l; location.hash = "#/journey"; }; tr.onclick = go; tr.onkeydown = (e) => { if (e.key === "Enter") go(); }; });
  };
  ["#q", "#fs", "#fo"].forEach((s) => $(s).addEventListener("input", draw)); draw();
}

/* ---------------------------------------------------------------- report */

function pageReport() {
  setMain(`<div class="page-head reveal"><span class="eyebrow">For the investment committee</span><h1>Trustee report</h1><p>A single page a trustee can forward. It is generated from the same verification you see on the Pool page.</p></div>
    <div class="row reveal" style="margin-bottom:20px"><a class="btn" href="/api/report" target="_blank" rel="noopener">Open in a new tab</a><button class="btn ghost" id="print">Print</button></div>
    <iframe class="report reveal" src="/api/report" title="Trustee report"></iframe>`);
  $("#print").onclick = () => { const f = $("iframe.report"); f.contentWindow.focus(); f.contentWindow.print(); };
}

/* ---------------------------------------------------------------- router */

const fail = (e) => `<div class="err-box" role="alert">${esc(e.message)}<div class="retry"><button class="btn ghost small" onclick="route()">Retry</button></div></div>`;

async function refreshHeader() {
  if (S.status && S.status.phase === "ready") { try { S.status = await api("/api/status"); } catch {} }
  renderHeader();
}

async function route() {
  const my = ++token;
  if (S.status && S.status.phase === "ready") { try { S.status = await api("/api/status"); } catch {} }
  const st = S.status;
  renderHeader();
  if (!st || st.phase === "idle" || st.phase === "error") return landing();
  if (st.phase === "starting") return progress();
  const hash = location.hash.replace("#/", "");
  let cur = hash.split("/")[0] || (S.mode === "story" ? "story" : "pool");
  if (cur === "story" && S.mode !== "story") { S.mode = "story"; store.set("ariadne-mode", "story"); renderHeader(); }
  if (S.mode === "story" && !PAGES[cur] && cur !== "story") cur = "story";
  ROOT_EL = null;
  window.scrollTo(0, 0);
  if (cur === "story") { try { await PAGES.story(+hash.split("/")[1] || 0, my); } catch (e) { if (my === token) setMain(fail(e)); } document.title = "Ariadne · Story"; return; }
  if (NEEDS_POOL.has(cur) && !st.poolBuilt && !S.snap) { location.hash = "#/build"; return; }
  setMain('<div class="skel"></div><div class="skel" style="margin-top:16px"></div>');
  try { await (PAGES[cur] || PAGES.pool)(); } catch (e) { if (my === token) setMain(fail(e)); }
  if (my === token) document.title = "Ariadne · " + ((ROUTES.find((r) => r[0] === cur) || ROUTES[0])[1]);
}

async function refresh() {
  S.status = await api("/api/status");
  if (S.status.phase === "starting") startPolling();
  if (S.status.phase === "ready" && !S.snap) { try { S.verify = S.status.poolBuilt ? await api("/api/verify", {}) : null; } catch {} }
  route();
}

function startPolling() {
  clearInterval(poll);
  poll = setInterval(async () => {
    try { S.status = await api("/api/status"); } catch { return; }
    if (S.status.phase === "starting") { progress(); return; }
    clearInterval(poll); S.pool = S.units = S.evidence = S.verify = S.metrics = null; S.results = {}; refresh();
  }, 800);
}

Object.assign(PAGES, { pool: pagePool, journey: pageJourney, attacks: pageAttacks, life: pageLife, evidence: pageEvidence, units: pageUnits, report: pageReport });

function backToTop() {
  const b = document.createElement("button");
  b.className = "btn ghost small totop"; b.textContent = "Back to top";
  b.onclick = () => window.scrollTo({ top: 0, behavior: "smooth" });
  document.body.appendChild(b);
  addEventListener("scroll", () => b.classList.toggle("show", scrollY > 700), { passive: true });
}

async function boot() {
  backToTop();
  window.addEventListener("hashchange", route);
  addEventListener("keydown", (e) => {
    if (S.mode !== "story" || /input|textarea|select/i.test(e.target.tagName)) return;
    const n = +(location.hash.split("/")[1] || 0);
    if (e.key === "ArrowRight" && PAGES.storyGo) PAGES.storyGo(n + 1);
    if (e.key === "ArrowLeft" && PAGES.storyGo) PAGES.storyGo(n - 1);
  });
  try { await refresh(); } catch (e) { setMain(`<div class="err-box" role="alert">Cannot reach the Ariadne server: ${esc(e.message)}</div>`); }
}
window.addEventListener("DOMContentLoaded", boot);
