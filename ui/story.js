"use strict";
/* Story mode: the guided demo from the spec (hook, one invoice, attacks, honest pool, trustee check, rules from prose).
   Each act embeds the same live pages the console uses, so nothing here is staged. All dynamic text passes through esc(). */

const ACTS = [
  { label: "The problem", h: "A supplier in Chennai waits 90 days to be paid.",
    p: "TReDS lets a bank pay her in two days. Since June that bank can resell her invoice, and soon it can be bundled with hundreds of others and sold to investors. Then the investor has to reconcile records from the pool's seller, its servicer and the registries. Ariadne lets them check the whole bundle themselves." },
  { label: "One invoice", h: "Follow one invoice.", p: "Every step is an on-chain event anyone can read. The graph is drawn from the events, not from a script." },
  { label: "Attacks", h: "Try to break it.", p: "Each attack is a real transaction. The contract refuses, and nothing moves. Choose which one to fire." },
  { label: "An honest pool", h: "Build an honest pool.", p: "The engine finds a fabricated loop and holds it for a person, keeps a real supply loop, and resolves four GSTINs into one buyer group. Concentration is trimmed to fit the cap, then the contract seals the pool." },
  { label: "The trustee", h: "The trustee checks it themselves.", p: "Recomputed from the chain, not from the originator's word. Then the pool changes: settlements, a default, a substitution, and a swap that breaks the cap." },
  { label: "Rules from prose", h: "Rules from prose.", p: "Legal clauses become rules, each tied to its source words. A person signs. The engine enforces only what was signed." },
  { label: "Close", h: "", p: "" },
];

function storyGo(n) {
  n = Math.max(0, Math.min(ACTS.length - 1, n));
  location.hash = `#/story/${n}`;
}
PAGES.storyGo = storyGo;

function ownershipGraph(events) {
  const holders = [];
  const push = (t, now) => { if (!holders.length || holders[holders.length - 1].t !== t) holders.push({ t, now }); };
  events.forEach((e) => {
    if (e.event === "UnitRegistered") push(`Platform ${e.registrar}`);
    else if (e.event === "UnitFinanced") push(`Financier ${e.financier}`);
    else if (e.event === "UnitTransferred") push(`Financier ${e.to}`);
    else if (e.event === "MembershipChanged" && /ADD|SUBSTITUTE_IN/.test(e.op)) push(e.pool);
    else if (e.event === "UnitSettled") push("Settled");
    else if (e.event === "UnitDefaulted") push("Defaulted");
  });
  if (holders.length) holders[holders.length - 1].now = true;
  const w = 150, gap = 60, W = 40 + holders.length * (w + gap);
  const nodes = holders.map((h, i) => {
    const x = 20 + i * (w + gap);
    return `<g class="n ${h.now ? "now" : ""}"><rect x="${x}" y="30" width="${w}" height="52" rx="8"/><text class="t" x="${x + w / 2}" y="52" text-anchor="middle">${esc(h.t)}</text><text x="${x + w / 2}" y="70" text-anchor="middle">${h.now ? "holds it now" : `step ${i + 1}`}</text></g>`;
  }).join("");
  const edges = holders.slice(1).map((_, i) => { const x1 = 20 + i * (w + gap) + w, x2 = x1 + gap; return `<path class="e" style="--k:${i}" d="M${x1 + 2},56 L${x2 - 4},56"/>`; }).join("");
  return `<svg class="ograph" viewBox="0 0 ${W} 112" role="img" aria-label="Ownership path of the invoice"><defs><marker id="ogah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--ink)"/></marker></defs>${edges}${nodes}</svg>`;
}

function needPool(root) {
  root.innerHTML = `<div class="card"><h3>This act needs a sealed pool.</h3><p style="color:var(--muted);margin:8px 0 18px">Build one now. You can build it automatically with the demo reviewer's defaults, or step through the review yourself in act 3.</p>
    <div class="row"><button class="btn" id="stbuild">Build it automatically</button><a class="btn ghost" href="#/story/3">Build it myself in act 3</a></div></div>`;
  $("#stbuild").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/build", { auto: true }); S.status = await api("/api/status"); route(); });
}

PAGES.story = async function story(n, my) {
  n = Math.max(0, Math.min(ACTS.length - 1, n | 0));
  const st = S.status, act = ACTS[n], built = st && st.poolBuilt;
  const dots = ACTS.map((a, i) => `<button ${i === n ? 'aria-current="step"' : ""} class="${i < n ? "done" : ""}" data-go="${i}" aria-label="Act ${i}: ${esc(a.label)}" title="${esc(a.label)}"></button>`).join("");
  ROOT_EL = null;
  setMain(`<div class="story-shell"><div class="story-bar" role="group" aria-label="Story progress">${dots}</div>
    <div class="story-stage"><div class="story-act">${n < 6 ? `Act ${n} of ${ACTS.length - 2}` : "Close"} · ${esc(act.label)}</div><div id="storyhead"></div><div id="act"></div></div>
    <div class="story-nav"><button class="btn ghost" id="sprev" ${n === 0 ? "disabled" : ""}>Back</button><span class="k">arrow keys also work</span><button class="btn" id="snext" ${n === ACTS.length - 1 ? "disabled" : ""}>Next</button></div></div>`);
  $$("[data-go]").forEach((b) => b.onclick = () => storyGo(+b.dataset.go));
  $("#sprev").onclick = () => storyGo(n - 1);
  $("#snext").onclick = () => storyGo(n + 1);
  const head = $("#storyhead"), body = $("#act");
  const setHead = (h, p) => { head.innerHTML = `<h1 class="story-h">${esc(h)}</h1><p class="story-p">${esc(p)}</p>`; };
  document.title = "Ariadne · " + act.label;
  if (n < 6) setHead(act.h, act.p);

  if (n === 0) {
    if (built) {
      const t = await api("/api/trace?unit=" + encodeURIComponent(st.heroLabel));
      if (my !== token) return;
      const u = t.unit;
      body.innerHTML = `<div class="hero-card"><div class="card"><span class="tag">Hero invoice</span><h3 style="margin:12px 0 4px">${esc(u.label)}</h3>
        <dl class="kv" style="margin-top:14px"><dt>Seller</dt><dd>${esc(u.seller)}</dd><dt>Buyer</dt><dd>${esc(u.buyer)}</dd><dt>Face value</dt><dd>${inr(u.amountPaise)}</dd><dt>Due</dt><dd>${fmtDate(u.dueDate)}</dd><dt>Now held by</dt><dd>Financier ${esc(u.owner)}</dd></dl></div>
        <div><div class="callout">If the invoice isn't yours, it can't go in the pool.</div><p style="color:var(--muted)">Ariadne is the ${gloss("originator", "pool")} layer above CERSAI and MonetaGo: it traces every receivable and proves the pool is still what was sold.</p></div></div>`;
    } else {
      body.innerHTML = `<div class="callout">If the invoice isn't yours, it can't go in the pool.</div><p style="color:var(--muted);max-width:640px">Once a pool is built, the hero invoice appears here with its real numbers.</p>`;
    }
    return;
  }

  if (n === 1) {
    if (!built) return needPool(body);
    const t = await api("/api/trace?unit=" + encodeURIComponent(st.heroLabel));
    if (my !== token) return;
    const tl = t.events.map((e) => { const x = EVT[e.event](e); return `<li><span class="dot ${x.tone}">${icon(x.i)}</span><div class="tl-t">${esc(x.t)}</div><div class="tl-m">${esc(x.m)} <span class="mono">block ${e.block}</span></div></li>`; }).join("");
    body.innerHTML = `<div class="card">${ownershipGraph(t.events)}</div><div class="card" style="margin-top:16px"><ul class="timeline">${tl}</ul></div>`;
    return;
  }

  if (!built && n !== 3 && n !== 5) return needPool(body);
  ROOT_EL = body;
  if (n === 2) await PAGES.attacks();
  if (n === 3) await (built ? PAGES.evidence() : PAGES.build());
  if (n === 4) {
    const v = S.verify || (S.verify = await api("/api/verify", {}));
    const ok = v.status === "VERIFIED";
    await PAGES.life();
    body.insertAdjacentHTML("afterbegin", `<div class="verdict ${ok ? "ok" : "bad"}" style="margin-bottom:32px"><div><h2>${icon(ok ? "check" : "x", 24)}${ok ? "Verified" : "Not verified"}</h2><p>${ok ? `All checks pass at manifest v${v.version}.` : `Failed: ${esc(v.failed.join(", "))}.`}</p></div><div class="actions"><a class="btn" href="#/pool">Open every check</a></div></div>`);
  }
  if (n === 5) await PAGES.compiler();
  if (n === 6) {
    ROOT_EL = null;
    const m = built ? await api("/api/metrics") : null;
    const h = m && m.headline;
    head.innerHTML = `<h1 class="story-h">${h ? `<span class="headline"><b>${h.caught}</b> of <b>${h.planted}</b> planted failures caught, <b>${h.unauthorised}</b> unauthorised state changes.</span>` : "Trace every receivable. Pin every rule. Prove every pool."}</h1>
      <p class="story-p">${h ? `Computed from this run (${h.exercised} scenarios exercised so far). Nothing here was typed in.` : "Build the pool and run the acts to fill in the numbers."}</p>`;
    body.innerHTML = `<div class="callout">If the invoice isn't yours, it can't go in the pool, and any investor can prove what's in it.</div>
      <div class="row" style="margin-top:28px"><a class="btn" href="#/pool" id="tocon">Open the console</a><a class="btn ghost" href="/api/report" target="_blank" rel="noopener">Trustee report</a><a class="btn ghost" href="#/qa" id="toqa">Judge Q&amp;A</a></div>`;
    ["tocon", "toqa"].forEach((id) => { $("#" + id).onclick = () => { S.mode = "console"; store.set("ariadne-mode", "console"); }; });
  }
};
