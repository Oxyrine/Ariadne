"use strict";
/* Console pages added in v2: build wizard, compiler, metrics, context, Q&A, richer evidence.
   Shares helpers with app.js (esc, api, setMain, ...). All dynamic text passes through esc(). */

function ruleLine(r) {
  const v = r.value;
  return ({
    state_required: `Unit must be ${v} when pooled`, tenor_max_days: `Invoice tenor at most ${v} days`,
    min_days_to_maturity: `At least ${v} days left when pooled`, max_transfer_count: `Re-discounted at most ${v} times`,
    buyer_rating_min: `Buyer rated ${v} or better`, evidence_score_max: `Evidence score at most ${v}, unless a reviewer clears it`,
    buyer_group_concentration_max_bps: `No resolved buyer group above ${pct(v)} of the pool`,
    seller_concentration_max_bps: `No single seller above ${pct(v)} of the pool`, min_pool_size: `At least ${v} units when the pool is sealed`,
  })[r.kind] || `${r.kind}: ${v}`;
}
ruleText = ruleLine;  // the Pool page uses the fuller wording too

const short = (s, n = 16) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const decided = (m, k) => (m && m[k]) || null;

/* ---------------------------------------------------------- loop diagram */

function loopSVG(legs, legit) {
  const n = legs.length, cx = 190, cy = 105, rx = 135, ry = 66;
  const P = legs.map((_, i) => { const a = -Math.PI / 2 + (i * 2 * Math.PI) / n; return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]; });
  const edges = legs.map((l, i) => {
    const [x1, y1] = P[i], [x2, y2] = P[(i + 1) % n];
    const dx = x2 - x1, dy = y2 - y1, d = Math.hypot(dx, dy) || 1, off = 30;
    const sx = x1 + (dx / d) * off, sy = y1 + (dy / d) * off * 0.6, ex = x2 - (dx / d) * off, ey = y2 - (dy / d) * off * 0.6;
    return `<path class="edge" d="M${sx},${sy} L${ex},${ey}" marker-end="url(#lh${legit ? "g" : "r"})"/><text class="amt" x="${(sx + ex) / 2}" y="${(sy + ey) / 2 - 4}" text-anchor="middle">${esc(inrShort(l.amountPaise))}</text>`;
  }).join("");
  const nodes = legs.map((l, i) => `<g><rect class="node" x="${P[i][0] - 30}" y="${P[i][1] - 11}" width="60" height="22" rx="4"/><text x="${P[i][0]}" y="${P[i][1] + 4}" text-anchor="middle">${esc(short(l.from, 10))}</text></g>`).join("");
  return `<svg class="loopdiag ${legit ? "legit" : ""}" viewBox="0 0 380 210" role="img" aria-label="Trading loop through ${n} entities"><defs><marker id="lh${legit ? "g" : "r"}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="${legit ? "var(--green)" : "var(--red)"}"/></marker></defs>${edges}${nodes}</svg>`;
}

/* ---------------------------------------------------------- evidence page */

async function pageEvidence2() {
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
      <h3 style="margin-top:12px">${esc(c.id)}: loop of ${c.entities.length} entities</h3></div><div class="big-score">${c.score}</div></div>
      ${loopSVG(c.legs, !held)}
      <div class="scorebar" role="img" aria-label="Score ${c.score} of 1000, cut-off ${ev.cutoff}" style="margin-top:10px">${bar}<div class="cap" style="left:${ev.cutoff / 10}%"></div></div>
      <div class="caplabel" style="margin-top:6px;text-align:right">cut-off ${ev.cutoff}</div><div class="legend">${legend}</div>
      <div style="margin-top:18px"><div class="tl-m" style="margin-bottom:8px">${c.units.length} units in this loop</div><div class="chips">${chips}</div></div></div>`;
  }).join("");
  const link = (l) => { const [k, v, m] = l.split(":"); const label = { same_pan: "Same PAN", shared_director: "Shared director", shared_bank: "Shared bank account", shared_address: "Shared address (confirmed)" }[k] || k; return `${label} <code>${esc(v)}</code> across ${m.split(",").length} GSTINs`; };
  const grp = ev.groups.map((g) => `<div class="card reveal"><span class="tag yellow">Resolved group</span><h3 style="margin:12px 0 6px">${esc(g.id)}: ${g.members.length} entities, one economic group</h3>
    <p class="entity-list">${g.members.map(esc).join(", ")}</p><ul style="padding:0;list-style:none;margin:12px 0 0;color:var(--muted);font-size:14px">${g.links.map((l) => `<li>${link(l)}</li>`).join("")}</ul></div>`).join("");
  const cands = ev.candidates.map((c) => `<div class="card reveal"><span class="tag ${c.status === "CONFIRMED" ? "green" : c.status === "REJECTED" ? "red" : ""}">${esc(c.status)}</span>
    <h3 style="margin:12px 0 6px">${c.memberNames.map(esc).join(" and ")}</h3><p class="entity-list">${c.signals.map(esc).join(" · ")}</p>
    <p class="tl-m" style="margin:8px 0 0">A medium signal only creates a candidate. A person decides before the group map is frozen.</p></div>`).join("");
  setMain(`<div class="page-head reveal"><span class="eyebrow">Evidence, not verdicts</span><h1>Finding loops is easy. Telling real ones from fake ones is the hard part.</h1>
    <p>Every loop gets a score with its working shown. The weights are hand-set, not trained, and a person decides what happens to each held unit.</p></div>
    <section><div class="sec-head reveal"><div><h2>Trading loops</h2><p>Bars show how the score is made up. Loops above the cut-off are held for a reviewer. Red arrows are a held loop; green arrows are a loop that scored low and stays in.</p></div></div>
    <div class="grid" style="grid-template-columns:1fr 1fr;align-items:start">${cyc || '<p class="empty">No trading loops found in this dataset.</p>'}</div></section>
    <section><div class="sec-head reveal"><div><h2>Buyer groups</h2><p>Strong signals merge automatically: the same PAN under two GSTINs, a shared director, a shared bank account. The concentration cap applies to the group.</p></div></div>
    <div class="grid" style="grid-template-columns:1fr 1fr;align-items:start">${grp || '<p class="empty">No entities were merged on strong signals in this dataset.</p>'}</div></section>
    <section><div class="sec-head reveal"><div><h2>Candidate merges</h2><p>Entities that share only a registered address. Similar legal names add weight but never merge on their own.</p></div></div>
    <div class="grid" style="grid-template-columns:1fr 1fr;align-items:start">${cands || '<p class="empty">No candidates in this dataset.</p>'}</div></section>`);
}

/* ---------------------------------------------------------- build wizard */

const W = { step: 0, data: null, filter: "all", all: false };
const WSTEPS = ["Propose", "Rules", "Evaluate", "Review loops", "Groups", "Build and seal"];

async function pageBuild() {
  const st = S.status;
  if (st.poolBuilt) return builtSummary();
  if (st.build && st.build.running) return buildProgress();
  W.data = await api("/api/propose", {});
  drawWizard();
}

function builtSummary() {
  setMain(`<div class="page-head reveal"><span class="eyebrow">Build</span><h1>The pool is sealed.</h1><p>It was built through the steps below with the rules and review decisions you can see on the Pool and Evidence pages. To build a different pool, reset the session.</p></div>
    <div class="row reveal"><a class="btn" href="#/pool">Open the pool</a><a class="btn ghost" href="#/evidence">See the evidence</a></div>`);
}

function stepper() {
  return `<div class="stepper" role="tablist" aria-label="Build steps">${WSTEPS.map((s, i) => `<button role="tab" class="${i < W.step ? "done" : ""}" ${i === W.step ? 'aria-current="step"' : ""} data-step="${i}"><span class="n">${i + 1}</span>${s}</button>`).join("")}</div>`;
}

function drawWizard() {
  const d = W.data, c = d.counts;
  const foot = `<div class="wiz-foot"><button class="btn ghost" id="prev" ${W.step === 0 ? "disabled" : ""}>Back</button>${W.step < 5 ? '<button class="btn" id="next">Continue</button>' : ""}</div>`;
  const panels = [wProposal, wRules, wEvaluate, wLoops, wGroups, wBuild];
  setMain(`<div class="page-head reveal"><span class="eyebrow">Build an honest pool</span><h1>Six steps from a list of units to a sealed pool.</h1>
    <p>The originator proposes; the ledger confirms ownership; signed rules are pinned; the engine evaluates every unit; a person reviews what the evidence flags; then the contract seals the pool.</p></div>
    ${stepper()}<div id="panel">${panels[W.step](d, c)}</div>${foot}
    <div class="row" style="margin-top:32px"><button class="btn ghost small" id="autobuild">Skip the review: use the demo reviewer's defaults and build</button></div>`);
  $$("[data-step]").forEach((b) => b.onclick = () => { W.step = +b.dataset.step; drawWizard(); });
  if ($("#prev")) $("#prev").onclick = () => { W.step--; drawWizard(); };
  if ($("#next")) $("#next").onclick = () => { W.step++; drawWizard(); };
  $("#autobuild").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/build", { auto: true }); S.status = await api("/api/status"); route(); });
  bindWizard();
}

const stat = (k, v, s) => `<div class="card stat reveal"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s || ""}</div></div>`;

function wProposal(d, c) {
  const mine = d.units.filter((u) => u.ownerOk && !u.spare).length, spares = d.units.filter((u) => u.spare).length;
  const away = d.units.filter((u) => !u.ownerOk).length;
  return `<div class="grid g4">${stat("Units on the ledger", c.total, "Registered by three platforms")}${stat("Owned by the originator", mine, "Financier B, confirmed on-chain")}
    ${stat("Held back as spares", spares, "For substitutions after sealing")}${stat("Owned by someone else", away, "The contract will refuse these")}</div>
    <div class="card reveal" style="margin-top:16px"><h3>What happens here</h3><p style="color:var(--muted);margin-top:8px">The originator (financier B) proposes the units it owns. Ownership is read from the ledger, not from the originator's file, so a unit re-discounted to another financier cannot slip in. Spares are kept out of the first pool on purpose.</p></div>`;
}

function wRules(d) {
  const src = d.rulesSource === "compiled" ? "Compiled from a prospectus and signed by a reviewer" : "The default rule set, in the order of the reviewed prospectus";
  return `<div class="card reveal"><span class="tag ${d.rulesSource === "compiled" ? "yellow" : ""}">${d.rulesSource === "compiled" ? "Compiled" : "Default"}</span>
    <h3 style="margin:12px 0 4px">${esc(src)}</h3><p class="hash">${esc(d.rules.signedBy)}</p>
    <div style="margin-top:14px">${d.rules.rules.map((r) => `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line)"><span>${esc(ruleLine(r))}</span><code>${esc(r.id)} · ${esc(r.kind)}</code></div>`).join("")}</div>
    <div class="row" style="margin-top:18px"><button class="btn ghost" id="defrules">Use and sign the default rules</button><a class="btn ghost" href="#/compiler">Compile rules from a prospectus</a></div>
    <p class="tl-m" style="margin-top:12px">Whichever you use, the reviewer's signature covers the exact rule set and its hash is pinned to the pool on-chain. A rule cannot change afterwards.</p></div>`;
}

function wEvaluate(d, c) {
  const kinds = {};
  d.units.forEach((u) => u.fails.forEach((f) => { kinds[f.kind] = (kinds[f.kind] || 0) + 1; }));
  const f = W.filter;
  let rows = d.units.filter((u) => f === "all" || (f === "pass" ? u.ok : !u.ok));
  const total = rows.length;
  if (!W.all) rows = rows.slice(0, 40);
  const hist = Object.entries(kinds).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<div class="barrow"><span><code>${esc(k)}</code></span><div class="track"><div class="fill" style="width:${Math.min(100, (n / c.total) * 100 * 3)}%"></div></div><span class="num mono">${n}</span></div>`).join("");
  return `<div class="grid g4">${stat("Pass every unit rule", c.eligible, "Before concentration trimming")}${stat("Fail a rule", c.total - c.eligible, "With the reason shown below")}${stat("Held for review", c.held, "Trading-loop evidence")}${stat("Pending review", c.pendingLoops, "Excluded until decided")}</div>
    <div class="card reveal" style="margin-top:16px"><h3 style="margin-bottom:14px">Why units fail</h3><div class="bars">${hist || "<p class='empty'>Nothing fails.</p>"}</div></div>
    <div class="card tablewrap reveal" style="margin-top:16px;padding:8px"><div class="row" style="padding:10px 12px"><div class="seg-ctl">${["all", "pass", "fail"].map((x) => `<button data-f="${x}" aria-pressed="${f === x}">${x === "all" ? "All" : x === "pass" ? "Pass" : "Fail"}</button>`).join("")}</div><span class="tl-m">${total} units</span></div>
    <table class="tbl"><thead><tr><th>Unit</th><th>Seller → buyer</th><th>Rating</th><th>Verdict</th></tr></thead><tbody>${rows.map((u) => `<tr><td class="mono">${esc(u.label)}</td><td>${esc(u.seller)} → ${esc(u.buyer)}</td><td>${esc(u.rating || "-")}</td>
      <td>${u.ok ? '<span class="tag green">Pass</span>' : u.spare ? '<span class="tag">Spare</span>' : !u.ownerOk ? `<span class="tag red">Owned by ${esc(u.owner || "?")}</span>` : `<span class="tag red">${esc(u.fails[0] ? u.fails[0].rule : "Fail")}</span> <span class="tl-m">${esc(u.fails[0] ? u.fails[0].detail : "")}</span>`}</td></tr>`).join("")}</tbody></table>
    ${total > 40 ? `<div class="row" style="padding:12px"><button class="btn ghost small" id="showall">${W.all ? "Show fewer" : `Show all ${total}`}</button></div>` : ""}</div>`;
}

function wLoops(d) {
  if (!d.loops.length) return '<p class="empty">No trading loops in this dataset.</p>';
  const seg = Object.keys(FEATURES);
  return d.loops.map((l) => {
    const bar = seg.map((k, i) => `<span class="seg${i}" style="width:${(l.features[k] || 0) / 10}%"></span>`).join("");
    const units = l.units.map((u) => {
      const cur = decided(d.decisions.review, u.uid) || (u.review === "EXCLUDED" || u.review === "CLEARED" ? u.review : null);
      return `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line)"><span class="mono">${esc(u.label)}</span>${l.held ? `<div class="rowbtns"><button class="ex" data-rv="${esc(u.uid)}" data-d="EXCLUDED" aria-pressed="${cur === "EXCLUDED"}">Exclude</button><button class="cl" data-rv="${esc(u.uid)}" data-d="CLEARED" aria-pressed="${cur === "CLEARED"}">Clear</button></div>` : '<span class="tag green">Kept</span>'}</div>`;
    }).join("");
    return `<div class="card reveal" style="margin-bottom:16px"><div class="scorehead"><div><span class="tag ${l.held ? "red" : "green"}">${l.held ? "Held for review" : "Below the cut-off, kept"}</span><h3 style="margin-top:10px">${esc(l.id)}: ${l.entities.map(esc).join(" → ")}</h3></div><div class="big-score">${l.score}</div></div>
      <div class="scorebar" style="margin-bottom:14px">${bar}<div class="cap" style="left:70%"></div></div>${units}
      ${l.held ? `<div class="row" style="margin-top:14px"><button class="btn ghost small" data-all="EXCLUDED" data-loop="${esc(l.id)}">Exclude all</button><button class="btn ghost small" data-all="CLEARED" data-loop="${esc(l.id)}">Clear all</button></div>` : ""}</div>`;
  }).join("") + `<p class="tl-m">A unit that is held and undecided stays out of the pool. Clearing a unit is a human decision, recorded in the pinned evidence set.</p>`;
}

function wGroups(d) {
  const cap = d.capBps;
  const bars = (list) => list.map((g) => `<div class="barrow"><span>${esc(g.name)}${g.merged ? ' <span class="tag yellow">Group</span>' : ""}</span><div class="track"><div class="fill ${g.bps > cap ? "over" : g.merged ? "merged" : ""}" style="width:${Math.min(100, (g.bps / Math.max(2500, cap * 2)) * 100)}%"></div><div class="cap" style="left:${(cap / Math.max(2500, cap * 2)) * 100}%"></div></div><span class="num mono">${pct(g.bps)}</span></div>`).join("");
  const cands = d.candidates.length ? d.candidates.map((c) => `<div class="card reveal" style="margin-bottom:12px"><div class="row" style="justify-content:space-between;gap:16px"><div><h3>${c.memberNames.map(esc).join(" and ")}</h3><p class="entity-list" style="margin:6px 0 0">${c.signals.map(esc).join(" · ")}</p></div>
    <div class="rowbtns"><button class="cl" data-cand="${esc(c.id)}" data-d="CONFIRMED" aria-pressed="${c.status === "CONFIRMED"}">Same group</button><button class="ex" data-cand="${esc(c.id)}" data-d="REJECTED" aria-pressed="${c.status === "REJECTED"}">Separate</button></div></div></div>`).join("") : '<p class="empty">No candidate merges.</p>';
  return `<div class="card reveal"><h3>Candidate merges awaiting a decision</h3><p style="color:var(--muted);margin:6px 0 16px">Only strong signals merge on their own. A shared address is a medium signal, so a person decides.</p>${cands}</div>
    <div class="card reveal" style="margin-top:16px"><h3 style="margin-bottom:6px">Resolved groups (${d.groups.length})</h3>${d.groups.map((g) => `<p class="entity-list" style="margin:4px 0"><b style="color:var(--ink)">${esc(g.id)}</b> ${g.members.map(esc).join(", ")}</p>`).join("") || "<p class='empty'>None.</p>"}</div>
    <div class="split" style="margin-top:16px"><div class="card reveal"><h3 style="margin-bottom:14px">Before trimming</h3><div class="bars">${bars(d.shares.before)}</div></div><div class="card reveal"><h3 style="margin-bottom:14px">After trimming to 90% of the cap</h3><div class="bars">${bars(d.shares.after)}</div></div></div>
    <p class="tl-m" style="margin-top:12px">Dashed line: the ${pct(cap)} cap on each resolved group. The engine trims the smallest units of an over-cap group until it fits.</p>`;
}

function wBuild(d, c) {
  const ok = d.sealedSize >= d.minSize;
  return `<div class="grid g4">${stat("Pass unit rules", c.eligible, "")}${stat("After concentration trim", c.afterTrim, `${c.trimmed} trimmed out`)}${stat("Minimum pool size", d.minSize, ok ? "Satisfied" : "Not satisfied")}${stat("Undecided items", c.pendingLoops + c.pendingCandidates, "Loops and candidate merges")}</div>
    <div class="card reveal" style="margin-top:16px"><h3>Attest, add and seal</h3><p style="color:var(--muted);margin:8px 0 18px">The engine signs an attestation for each unit against the proposed sealed pool. The contract checks each signature, refuses anything that fails ownership or single-pool, and computes the commitment itself. Then the pool is sealed at manifest v1.</p>
    ${ok ? "" : `<div class="err-box">The pool would hold ${d.sealedSize} units, below the minimum of ${d.minSize}. Review decisions or rules need to change.</div>`}
    <button class="btn" id="dobuild" ${ok ? "" : "disabled"}>Attest, add and seal the pool</button></div>`;
}

function bindWizard() {
  const redraw = (data) => { W.data = data; drawWizard(); };
  const d = W.data;
  if ($("#defrules")) $("#defrules").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/rules/default", {}); redraw(await api("/api/propose", {})); toast("Default rules signed"); });
  $$("[data-f]").forEach((b) => b.onclick = () => { W.filter = b.dataset.f; W.all = false; drawWizard(); });
  if ($("#showall")) $("#showall").onclick = () => { W.all = !W.all; drawWizard(); };
  $$("[data-rv]").forEach((b) => b.onclick = async () => {
    const cur = b.getAttribute("aria-pressed") === "true";
    redraw(await api("/api/review", { decisions: { [b.dataset.rv]: cur ? null : b.dataset.d } }));
  });
  $$("[data-all]").forEach((b) => b.onclick = async () => {
    const loop = d.loops.find((l) => l.id === b.dataset.loop), dec = {};
    loop.units.forEach((u) => { dec[u.uid] = b.dataset.all; });
    redraw(await api("/api/review", { decisions: dec }));
  });
  $$("[data-cand]").forEach((b) => b.onclick = async () => {
    const cur = b.getAttribute("aria-pressed") === "true";
    redraw(await api("/api/groups", { decisions: { [b.dataset.cand]: cur ? null : b.dataset.d } }));
  });
  if ($("#dobuild")) $("#dobuild").onclick = (e) => busy(e.currentTarget, async () => {
    await api("/api/build", { auto: false }); S.status = await api("/api/status"); route();
  });
}

function buildProgress() {
  const draw = () => {
    const b = S.status.build, stages = Object.entries(S.status.buildStages);
    const idx = stages.findIndex(([k]) => k === b.stage);
    const items = stages.map(([k, name], i) => `<li class="${i < idx ? "done" : i === idx ? "now" : ""}"><span class="pip">${i < idx ? icon("check") : ""}</span>${esc(name)}<span class="d">${i === idx && b.n > 1 ? `${b.i} of ${b.n}` : ""}</span></li>`).join("");
    const pctDone = idx < 0 ? 0 : Math.round(((idx + (b.n > 1 ? b.i / b.n : 0)) / stages.length) * 100);
    setMain(`<div class="page-head"><span class="eyebrow">Building</span><h1>Attesting and sealing the pool</h1><p>Each unit gets a signed attestation and a transaction to the contract.</p></div>
      <div class="card" style="max-width:640px"><div class="miniprog"><i style="width:${pctDone}%"></i></div><ul class="steps">${items}</ul>${b.error ? `<div class="err-box">${esc(b.error)}</div>` : ""}</div>`);
  };
  draw();
  const t = setInterval(async () => {
    S.status = await api("/api/status");
    if (S.status.build.error) { clearInterval(t); draw(); return; }
    if (S.status.poolBuilt) { clearInterval(t); S.pool = S.units = S.evidence = S.verify = S.metrics = null; S.results = {}; if (S.mode !== "story") location.hash = "#/pool"; await refresh(); return; }
    draw();
  }, 800);
}

/* ---------------------------------------------------------- compiler */

const CMP = { data: null };

async function pageCompiler() {
  CMP.data = CMP.data || (await api("/api/compiler/parse", { text: "" }));
  drawCompiler();
}

function drawCompiler() {
  const c = CMP.data, approved = c.rules.filter((r) => r.status === "APPROVED" || r.status === "EDITED").length;
  const rules = c.rules.map((r) => `<div class="card reveal" style="margin-bottom:12px"><div class="row" style="justify-content:space-between;align-items:start;gap:16px">
    <div style="flex:1;min-width:240px"><span class="tag ${r.status === "APPROVED" ? "green" : r.status === "EDITED" ? "blue" : r.status === "REJECTED" ? "red" : ""}">${esc(r.status)}</span>
      <p style="margin:10px 0 6px;color:var(--muted)">Original clause</p><blockquote style="margin:0;padding-left:14px;border-left:2px solid var(--border2);color:var(--ink)">${esc(r.sourceSpan)}</blockquote></div>
    <div style="flex:1;min-width:240px"><p style="margin:0 0 6px;color:var(--muted)">Extracted rule</p><code>${esc(r.id)} · ${esc(r.kind)}</code>
      <div class="row" style="margin:10px 0"><label class="f" style="grid-auto-flow:column;align-items:center;gap:10px">Value<input type="text" data-val="${esc(r.id)}" value="${esc(r.value)}" size="8" aria-label="Value for ${esc(r.id)}"></label></div>
      <p style="margin:0;color:var(--body)">${esc(r.readBack)}</p>
      ${r.ambiguities.map((a) => `<p class="tl-m" style="margin:8px 0 0"><span class="tag yellow">Check</span> ${esc(a)}</p>`).join("")}</div></div>
    <div class="row" style="margin-top:14px"><button class="btn small" data-dec="${esc(r.id)}" data-s="APPROVED">Approve</button><button class="btn ghost small" data-dec="${esc(r.id)}" data-s="REJECTED">Reject</button></div></div>`).join("");
  const uns = c.unsupported.map((u) => `<div class="card tight reveal" style="margin-bottom:10px"><span class="tag red">Unsupported</span> <span style="margin-left:8px">${esc(u.span)}</span><p class="tl-m" style="margin:8px 0 0">${esc(u.reason)} A person must handle this clause; it is never silently dropped.</p></div>`).join("");
  setMain(`<div class="page-head reveal"><span class="eyebrow">Rules from prose</span><h1>Every rule traces to the words it came from.</h1>
    <p>Paste the eligibility section of an offering document. The parser proposes rules; a person approves, edits or rejects each one and signs the set. It never sees a unit and never decides one.</p></div>
    <div class="card tight reveal" style="margin-bottom:20px;border-color:var(--border3)"><span class="tag yellow">No AI in this build</span> <span style="margin-left:8px;color:var(--body)">This is a deterministic parser over a closed phrase grammar, standing in for the language-model compiler in the spec. The guarantees that matter are the same: verbatim source words, bounds checks, human sign-off.</span></div>
    <section><label class="f reveal">Offering-document text<textarea id="src" rows="10" style="font:14px/1.6 var(--mono);color:var(--ink);background:var(--card);border:1px solid var(--border2);border-radius:8px;padding:14px;width:100%">${esc(c.text)}</textarea></label>
    <div class="row reveal" style="margin-top:12px"><button class="btn" id="parse">Parse</button><button class="btn ghost" id="sample">Reset to the sample prospectus</button></div></section>
    <section><div class="sec-head reveal"><div><h2>Proposed rules (${c.rules.length})</h2><p>One row per rule. Nothing takes effect until you sign.</p></div></div>${rules || '<p class="empty">No rules matched.</p>'}
      <h2 style="margin:36px 0 14px" class="reveal">Unsupported clauses (${c.unsupported.length})</h2>${uns || '<p class="empty">Every clause mapped.</p>'}</section>
    <section class="reveal"><div class="card"><h3>Sign and use these rules</h3><p style="color:var(--muted);margin:8px 0 16px">${approved} of ${c.rules.length} rules approved. The signed set is pinned by hash to the pool you build next.</p>
      <button class="btn" id="signrules" ${approved ? "" : "disabled"}>Sign the approved rules</button><div id="signres" style="margin-top:16px"></div></div></section>`);
  const load = async (text) => { CMP.data = await api("/api/compiler/parse", { text }); drawCompiler(); };
  $("#parse").onclick = (e) => busy(e.currentTarget, () => load($("#src").value));
  $("#sample").onclick = (e) => busy(e.currentTarget, () => load(""));
  $$("[data-dec]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.dec, input = $(`[data-val="${id}"]`), rule = c.rules.find((r) => r.id === id);
    const decision = { status: b.dataset.s };
    if (b.dataset.s === "APPROVED") {
      const raw = input.value.trim(), v = /^-?\d+$/.test(raw) ? +raw : raw;
      if (String(v) !== String(rule.value)) decision.value = v;
    }
    try { CMP.data = await api("/api/compiler/decide", { decisions: { [id]: decision } }); } catch (e) { toast(e.message); }
    drawCompiler();
  });
  $("#signrules").onclick = (e) => busy(e.currentTarget, async () => {
    try {
      const r = await api("/api/compiler/sign", {});
      $("#signres").innerHTML = `<span class="tag green">Signed</span><p class="hash" style="margin-top:10px">Signer ${esc(r.signer)}<br>Rules hash ${esc(r.hash)}</p><p class="tl-m">${r.rules.rules.length} rules pinned. ${S.status.poolBuilt ? "The current pool was already sealed with other rules; reset to use these." : "Build the pool to use them."}</p>${S.status.poolBuilt ? "" : '<a class="btn" href="#/build" style="margin-top:8px">Continue to Build</a>'}`;
    } catch (err) { $("#signres").innerHTML = `<div class="err-box">${esc(err.message)}</div>`; }
  });
}

/* ---------------------------------------------------------- metrics */

async function pageMetrics() {
  const m = S.metrics = await api("/api/metrics");
  const h = m.headline;
  const sc = m.scenarios.map((s) => `<div class="metric-row"><span>${esc(s.name)}</span><span class="tag ${s.status === "caught" ? "green" : s.status === "missed" ? "red" : ""}">${esc(s.status)}</span></div>`).join("");
  const gu = m.guarantees.map((g) => `<div class="metric-row"><div><div>${esc(g.name)}</div>${g.note ? `<div class="tl-m">${esc(g.note)}</div>` : ""}</div>${g.value === null ? '<span class="tl-m">Run it to measure</span>' : `<span class="${g.value === 0 ? "zero" : "big-score"}" style="${g.value === 0 ? "" : "color:var(--red)"}">${g.value}</span>`}</div>`).join("");
  const rows = [["fab_recall", "Fabricated-loop recall"], ["legit_fp", "Legitimate-loop false positives"], ["er_precision", "Entity-resolution precision (strong signals only)"], ["er_recall", "Entity-resolution recall (strong signals only)"], ["er_recall_confirmed", "Recall after the reviewer confirms candidates"]];
  const det = rows.map(([k, n]) => `<tr><td>${n}</td>${[..."ABC"].map((s) => `<td class="mono">${esc(m.detection[s][k])}</td>`).join("")}</tr>`).join("");
  const max = Math.max(...m.benchmark.map((b) => b.ms));
  const bench = m.benchmark.map((b) => `<tr><td class="num mono">${b.edges}</td><td class="num mono">${b.cycles}</td><td>${b.truncated ? '<span class="tag yellow">Capped</span>' : "No"}</td><td class="num mono">${b.ms}</td></tr>`).join("");
  setMain(`<div class="page-head reveal"><span class="eyebrow">Measured, not claimed</span><h1>What the system guarantees, and where it stops being reliable.</h1><p>Two kinds of number, never mixed. Guarantees should be exactly zero because the contract and engine enforce them. Detection is measured on synthetic data, including the seed built to beat the heuristics.</p></div>
    <section><div class="card reveal"><div class="headline"><b>${h.planted}</b> planted integrity scenarios, <b>${h.caught}</b> caught so far (${h.exercised} exercised), <b>${h.unauthorised}</b> unauthorised state changes.</div><p class="tl-m" style="margin:14px 0 0">Computed from this session. Fire the attacks and lifecycle steps to exercise the rest.</p></div></section>
    <section class="two"><div class="card reveal"><h3 style="margin-bottom:6px">Planted scenarios</h3>${sc}</div><div class="card reveal"><h3 style="margin-bottom:6px">Guarantee metrics (should be zero)</h3>${gu}</div></section>
    <section class="reveal"><div class="sec-head"><div><h2>Detection metrics</h2><p>Seed A is the demo. Seed B was never used while tuning. Seed C is adversarial: asymmetric rings, old entities, a group linked only by a shared address. The misses are the point.</p></div></div>
      <div class="card tablewrap"><table class="tbl"><thead><tr><th>Metric</th><th>Seed A (demo)</th><th>Seed B (unseen)</th><th>Seed C (adversarial)</th></tr></thead><tbody>${det}</tbody></table></div></section>
    <section class="reveal"><div class="sec-head"><div><h2>Loop enumeration benchmark</h2><p>Runtime as one 30-node component gets denser, cycle length bounded at 6. The cap starts to bite where the runtime flattens.</p></div></div>
      <div class="two"><div class="card"><div class="hbar">${m.benchmark.map((b) => `<i style="height:${Math.max(6, (b.ms / max) * 100)}%" title="${b.ms} ms"></i>`).join("")}</div><div class="chart-l"><span>${m.benchmark[0].edges} edges</span><span>${m.benchmark[m.benchmark.length - 1].edges} edges</span></div></div>
      <div class="card tablewrap"><table class="tbl"><thead><tr><th class="num">Edges</th><th class="num">Cycles</th><th>Truncated</th><th class="num">ms</th></tr></thead><tbody>${bench}</tbody></table></div></div></section>
    <section class="reveal"><div class="sec-head"><div><h2>Operational comparison</h2><p>A hypothesis to demonstrate, not a measured result.</p></div></div>
      <div class="card tablewrap"><table class="tbl"><thead><tr><th>Task for the trustee</th><th>Spreadsheet review</th><th>Ariadne</th></tr></thead><tbody>${OPS.map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("")}</tbody></table></div></section>
    <p class="tl-m reveal">All numbers come from synthetic data we generated. They show the system does what it claims on known cases and where it fails on harder ones. They say nothing about real TReDS data, which we do not have.</p>`);
}

/* ---------------------------------------------------------- context, Q&A */

function archSVG() {
  const rows = LAYERS.map((l, i) => {
    const y = 20 + i * 62;
    return `<rect class="l ${l[2] ? "chain" : ""}" x="20" y="${y}" width="620" height="50" rx="8"/><text class="t" x="40" y="${y + 21}">${esc(l[0])}</text><text x="40" y="${y + 40}">${esc(l[1])}</text>${l[2] ? `<text class="t" x="620" y="${y + 21}" text-anchor="end">only layer that moves ownership</text>` : ""}`;
  }).join("");
  return `<svg class="arch" viewBox="0 0 660 340" role="img" aria-label="Ariadne's five layers">${rows}</svg>`;
}

async function pageContext() {
  setMain(`<div class="page-head reveal"><span class="eyebrow">Context</span><h1>Recording who owns a receivable is solved. Keeping a pool's promises true is not.</h1>
    <p>Where Ariadne sits next to what already exists, what it covers, and exactly what it does and does not claim.</p></div>
    <section class="reveal"><div class="sec-head"><div><h2>Where Ariadne sits</h2><p>${gloss("CERSAI")}, MonetaGo and the ${gloss("TReDS")} platforms cover the receivable itself. Ariadne is the pool layer above them. This table is our reading of their public material and is a hypothesis we verify before relying on it.</p></div></div>
      <div class="card tablewrap"><table class="tbl"><thead><tr><th>System</th><th>What it does</th><th>What it does not do, as far as we can find</th></tr></thead><tbody>${STATUS_QUO.map((r) => `<tr ${r.us ? 'style="background:var(--bone)"' : ""}><td><b style="color:var(--ink)">${esc(r.sys)}</b></td><td>${esc(r.does)}</td><td>${esc(r.doesnt)}</td></tr>`).join("")}</tbody></table></div></section>
    <section class="reveal"><div class="sec-head"><div><h2>Seven failures after financing</h2><p>Failure 1 is covered elsewhere and we never pitch it. The other six are where the pool layer works.</p></div></div>
      <div class="card tablewrap"><table class="tbl"><thead><tr><th>Failure</th><th>Example</th><th>Covered today by</th><th>Ariadne's role</th></tr></thead><tbody>${GAPS.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>
    <section class="reveal"><div class="sec-head"><div><h2>Architecture</h2><p>Five layers; only the ledger can change who owns a receivable or which pool it is in.</p></div></div>
      <div class="two"><div class="card">${archSVG()}</div><div class="card tablewrap"><table class="tbl"><thead><tr><th>Data</th><th>Where</th><th>Why</th></tr></thead><tbody>${ONCHAIN.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>
      <p style="margin-top:16px;max-width:760px;color:var(--body)">The prototype runs on a local chain for demo reliability. A production version would run on a permissioned consortium ledger shared by the platforms and financiers. A consortium database could implement the same rules; we choose a ledger because pool membership and ownership history must be append-only and jointly auditable after investors rely on them, not because the chain is the innovation.</p></section>
    <section class="reveal"><div class="sec-head"><div><h2>Trust model</h2><p>The claim is narrow on purpose.</p></div></div>
      <div class="callout">If the invoice isn't yours, it can't go in the pool.</div>
      <div class="two" style="margin-top:20px"><div class="card"><h3 style="margin-bottom:12px">Protects against</h3><ul class="plist">${PROTECTS.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div><div class="card"><h3 style="margin-bottom:12px">Does not prove or prevent</h3><ul class="plist">${NOT_PROVEN.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>
      <div class="card tablewrap" style="margin-top:16px"><table class="tbl"><thead><tr><th>Component</th><th>Trusted for</th><th>Not trusted for</th><th>If it is wrong or compromised</th></tr></thead><tbody>${TRUST.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`);
}

async function pageQA() {
  setMain(`<div class="page-head reveal"><span class="eyebrow">Questions a judge will ask</span><h1>Sixteen questions, each answered in under twenty seconds.</h1><p>Anyone who knows TReDS will ask about CERSAI or MonetaGo first.</p></div>
    <div class="acc reveal">${QA.map((q, i) => `<details ${i === 0 ? "open" : ""}><summary>${esc(q[0])}</summary><div class="ans">${esc(q[1])}</div></details>`).join("")}</div>`);
}

Object.assign(PAGES, { evidence: pageEvidence2, build: pageBuild, compiler: pageCompiler, metrics: pageMetrics, context: pageContext, qa: pageQA });
