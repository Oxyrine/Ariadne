"""Originator-side actions against the ledger: replay the dataset, build and seal a pool,
fire the attacks, run the post-issuance lifecycle. Everything here goes through the contract;
only the contract can move ownership or pool membership."""
import json
from datetime import date
from pathlib import Path

from . import engine, evidence, generate, wire

POOL_NAME = "demo-pool-01"
TARGET_BPS = 900  # trim groups to 9% so the signed 10% cap has headroom
SETTLE_BPS = 950
RULES = {
    "ruleSetId": POOL_NAME, "version": 1,
    "rules": [
        {"id": "R1", "kind": "state_required", "value": "FINANCED"},
        {"id": "R2", "kind": "tenor_max_days", "value": 90},
        {"id": "R3", "kind": "min_days_to_maturity", "value": 15},
        {"id": "R4", "kind": "max_transfer_count", "value": 2},
        {"id": "R5", "kind": "buyer_rating_min", "value": "A-"},
        {"id": "R6", "kind": "evidence_score_max", "value": 700},
        {"id": "R7", "kind": "buyer_group_concentration_max_bps", "value": 1000, "basis": "resolved_group"},
        {"id": "R8", "kind": "seller_concentration_max_bps", "value": 500},
        {"id": "R9", "kind": "min_pool_size", "value": 100},
    ],
    "sourceClauses": {"R2": "4.2(a)", "R7": "4.3(b)"},
    "unsupported": [],
}


def uid_of(r): return wire.unit_id(r["platform"], r["unit_no"])
def label(r): return f"{r['platform']}:{r['unit_no']}"


def rkey_of(r):
    return wire.receivable_key(r["seller_gstin"], r["buyer_gstin"], r["invoice_no"], r["invoice_date"], r["amount_paise"])


SINK = print          # the web server swaps this to capture log lines
LAST = {}             # structured outcome of the last attack or swap
RESULT = {}           # structured outcome of the last lifecycle action


def say(*a): SINK(" ".join(str(x) for x in a))


# ----------------------------------------------------------------- replay

def setup(ch, units, progress=None):
    """Register, finance and re-discount every unit on the chain (the synthetic registrars' history)."""
    f = ch.contract.functions
    for i, r in enumerate(units):
        if progress:
            progress(i, len(units))
        uid = uid_of(r)
        ch.tx(f.registerUnit(uid, rkey_of(r), wire.epoch(r["invoice_date"]), wire.epoch(r["due_date"]),
                             r["amount_paise"], wire.party_key(r["buyer_gstin"]), wire.party_key(r["seller_gstin"])),
              r["platform"])
        ch.tx(f.recordFinancing(uid, ch.acct[r["financier"]]), r["platform"])
        owner = r["financier"]
        for nxt in filter(None, r["rediscount_path"].split(">")):
            ch.tx(f.rediscount(uid, ch.acct[nxt]), owner)
            owner = nxt


def view(ch, uid, state="FINANCED"):
    u = ch.unit(uid)
    return {"unitId": wire.hx(uid), "receivableKey": wire.hx(u["receivableKey"]),
            "buyerKey": wire.hx(u["buyerKey"]), "sellerKey": wire.hx(u["sellerKey"]), "state": state,
            "owner": u["owner"], "invoiceDate": u["invoiceDate"], "dueDate": u["dueDate"],
            "amountPaise": u["amountPaise"], "transferCount": u["transferCount"]}


# -------------------------------------------------------------- pool build

def default_group_decisions(ents):
    """Demo reviewer policy: confirm a candidate merge only when legal names also match."""
    cands = evidence.resolve_groups(ents)["candidates"]
    return {c["id"]: ("CONFIRMED" if any("similar" in x for x in c["signals"]) else "REJECTED") for c in cands}


def prepare(seed_dir: Path, review=None, group_decisions=None):
    """Score evidence, resolve groups, apply the human review. review is {unitIdHex: EXCLUDED|CLEARED};
    group_decisions is {candidateId: CONFIRMED|REJECTED}. None means the demo reviewer's defaults."""
    units, ents, meta, truth = generate.load(seed_dir)
    ev, diag = evidence.score_units(units, ents, date.fromisoformat(meta["anchor"]))
    if group_decisions is None:
        group_decisions = default_group_decisions(ents)
    gm = evidence.resolve_groups(ents, group_decisions)
    ev = evidence.annotate(ev, units, gm)
    held = {uid for uid, e in ev.items() if e["reviewStatus"] == "PENDING_REVIEW"}
    if review is None:
        review = {uid: "EXCLUDED" for uid in held}
    ev = evidence.review(ev, {uid: d for uid, d in review.items() if uid in held})
    return {"units": units, "ents": ents, "meta": meta, "truth": truth, "ev": ev, "gm": gm, "diag": diag,
            "held": held, "group_decisions": group_decisions}


def sign_rules(ch, name=POOL_NAME):
    rs = engine.sign_ruleset({**RULES, "ruleSetId": name}, ch.key["rules"])
    engine.validate_ruleset(rs)
    return rs


def eligible(ch, prep, rs, at=None):
    """Units the B financier could put forward: owned by B, not held back as spares, passing every unit rule."""
    at = at or ch.ts() + 60
    out = []
    for r in prep["units"]:
        uid = uid_of(r)
        v = view(ch, uid)
        fails = engine.check_unit(rs, v, at, prep["ev"])
        out.append({"row": r, "uid": uid, "view": v, "fails": fails,
                    "ok": not fails and not r["reserve"] and v["owner"] == ch.acct["B"]})
    return out


def trim(members, gm, rs):
    """Trim each concentration rule to 90% of its cap, smallest units first, until stable."""
    caps = {r["kind"]: r["value"] * 9 // 10 for r in rs["rules"] if r["kind"].endswith("_bps")}
    keyfs = []
    if "buyer_group_concentration_max_bps" in caps:
        keyfs.append((lambda m: engine.group_of(m, gm), caps["buyer_group_concentration_max_bps"]))
    if "seller_concentration_max_bps" in caps:
        keyfs.append((lambda m: m["sellerKey"], caps["seller_concentration_max_bps"]))
    members, dropped = list(members), []
    while True:
        total = sum(m["amountPaise"] for m in members) or 1
        for keyf, target in keyfs:
            by = {}
            for m in members:
                by.setdefault(keyf(m), []).append(m)
            g, ms = max(by.items(), key=lambda kv: sum(m["amountPaise"] for m in kv[1]))
            if sum(m["amountPaise"] for m in ms) * 10_000 // total > target:
                out = min(ms, key=lambda m: m["amountPaise"])
                members.remove(out)
                dropped.append(out)
                break
        else:
            return members, dropped


def propose(ch, seed_dir: Path, review=None, group_decisions=None, rs=None):
    """Step 1 to 4 of the honest-pool workflow, without touching the chain's pool state."""
    prep = prepare(seed_dir, review, group_decisions)
    rs = rs or sign_rules(ch)
    rows = eligible(ch, prep, rs)
    return prep, rs, rows


def build_pool(ch, seed_dir: Path, name=POOL_NAME, review=None, group_decisions=None, rs=None, progress=None):
    prog = progress or (lambda *a: None)
    prep = prepare(seed_dir, review, group_decisions)
    ev, gm = prep["ev"], prep["gm"]
    say("Evidence scoring:")
    for uid in prep["held"]:
        e = ev[uid]
        say(f"  review: {e['label']} {e['cycleId']} score {e['evidenceScore']} -> {e['reviewStatus']}")
    for gid, g in gm["evidence"].items():
        say(f"  group {gid}: {len(g['members'])} GSTINs  links: {g['links']}")
    evdoc = evidence.evidence_doc(ev)
    prog("rules", 0, 1)
    rs = rs or sign_rules(ch, name)
    engine.validate_ruleset(rs)
    assert engine.ruleset_signer(rs)
    pid, rhash = wire.pool_id(name), wire.rules_hash(rs)
    prog("pool", 0, 1)
    ch.tx(ch.contract.functions.createPool(pid, rhash, wire.content_hash(gm), wire.content_hash(evdoc)), "B")

    at = ch.ts() + 60
    prog("select", 0, 1)
    rows = eligible(ch, prep, rs, at)
    cands = [x["view"] for x in rows if x["ok"]]
    say(f"Eligible on unit rules: {len(cands)} of {len(rows)} units")
    prog("trim", 0, 1)
    members, dropped = trim(cands, gm, rs)
    say(f"After concentration trim: {len(members)} units, largest group {engine.largest_group_bps(members, gm)} bps")
    pool_fails = engine.check_pool(rs, members, gm, sealing=True)
    if pool_fails:
        raise ValueError("cannot seal: " + "; ".join(f"{f['rule']} {f['detail']}" for f in pool_fails))

    ledger = ch.contract.address
    for i, m in enumerate(members):
        prog("attest", i, len(members))
        fails = engine.evaluate(rs, m, at, ev, members, gm, sealing=True)
        assert not fails, fails
        sig = engine.attest(ch.key["engine"], ch.chain_id, ledger, wire.hx(pid), m, wire.hx(rhash), 0)
        ch.tx(ch.contract.functions.addToPool(pid, wire.unhx(m["unitId"]), sig), "B")
    prog("seal", 0, 1)
    ch.tx(ch.contract.functions.sealPool(pid), "B")
    doc = {"poolId": wire.hx(pid), "ledger": ledger, "chainId": ch.chain_id, "rules": rs,
           "groupMap": gm, "evidence": evdoc, "synthetic": True}
    (seed_dir / "pool.json").write_text(json.dumps(doc, indent=1))
    say(f"Sealed {name}: {len(members)} members, manifest v1")
    return doc


# ---------------------------------------------------------------- context

class Ctx:
    def __init__(self, ch, seed_dir: Path):
        self.ch, self.dir = ch, seed_dir
        self.units, self.ents, self.meta, self.truth = generate.load(seed_dir)
        self.rows = {uid_of(r): r for r in self.units}
        self.doc = json.loads((seed_dir / "pool.json").read_text())
        self.pid = wire.unhx(self.doc["poolId"])
        self.rs, self.gm = self.doc["rules"], self.doc["groupMap"]
        self.ev = self.doc["evidence"]["units"]
        self.rhash = wire.hx(wire.rules_hash(self.rs))

    def by_label(self, lab):
        return next(u for u, r in self.rows.items() if label(r) == lab)

    def live(self):
        return [view(self.ch, u, "POOLED") for u in self.rows if self.ch.unit(u)["currentPoolId"] == self.pid]

    def version(self):
        return self.ch.contract.functions.pools(self.pid).call()[6]

    def sign(self, v, version):
        return engine.attest(self.ch.key["engine"], self.ch.chain_id, self.ch.contract.address,
                             wire.hx(self.pid), v, self.rhash, version)

    def reserve(self):
        out = []
        for u, r in self.rows.items():
            if r["reserve"]:
                v = view(self.ch, u)
                if v["owner"] == self.ch.acct["B"] and self.ch.unit(u)["state"] == 2:
                    out.append(v)
        return out


# ----------------------------------------------------------------- attacks

def _attack(ch, title, expect, action, uids, pids):
    say(f"\nATTACK: {title}")
    before = ch.snapshot(uids, pids)
    LAST.clear()
    try:
        action()
        say("  !! NOT REJECTED")
        LAST.update(title=title, error=None, ok=False, unchanged=False, expected=expect)
        return False
    except Exception as e:
        name = getattr(e, "name", None)
        say(f"  reverted: {e}")
        after = ch.snapshot(uids, pids)
        ok = name == expect and before == after
        say(f"  state before == after: {before == after}   expected {expect}: {name == expect}")
        LAST.update(title=title, error=name or str(e), args=[str(x) for x in getattr(e, "args_", [])],
                    expected=expect, unchanged=before == after, ok=ok,
                    blockBefore=before["block"], blockAfter=after["block"])
        return ok


def _open_pool(ctx, name="demo-pool-02"):
    """A second, unsealed pool owned by B: the place attacks are staged."""
    ch, pid2 = ctx.ch, wire.pool_id(name)
    if int(ch.contract.functions.pools(pid2).call()[0], 16) == 0:
        ch.tx(ch.contract.functions.createPool(pid2, wire.unhx(ctx.rhash), wire.content_hash(ctx.gm),
                                               wire.content_hash(ctx.doc["evidence"])), "B")
    return pid2


def attack_stale_owner(ctx):
    ch = ctx.ch
    pid2 = _open_pool(ctx)
    uid = ctx.by_label(ctx.truth["stale_owner_unit"])
    v = view(ch, uid)
    sig = engine.attest(ch.key["engine"], ch.chain_id, ch.contract.address, wire.hx(pid2), v, ctx.rhash, 0)
    say(f"  {ctx.truth['stale_owner_unit']} was re-discounted B -> C (owner now {ch.names[ch.unit(uid)['owner']]})")
    return _attack(ch, "B pools a unit it already re-discounted to C", "NotOwner",
                   lambda: ch.tx(ch.contract.functions.addToPool(pid2, uid, sig), "B"), [uid], [pid2])


def attack_duplicate(ctx):
    ch = ctx.ch
    hero = next(r for r in ctx.units if label(r) == ctx.truth["hero"])
    dup = {**hero, "platform": "P2", "unit_no": "FU-999001", "invoice_no": f" {hero['invoice_no'].lower()} ",
           "seller_gstin": hero["seller_gstin"].lower(), "buyer_gstin": f" {hero['buyer_gstin']}"}
    same = wire.hx(rkey_of(dup)) == wire.hx(rkey_of(hero))
    say(f"  same invoice, different formatting, second platform; receivableKey identical: {same}")
    return _attack(ch, "second platform registers the hero invoice as a new unit", "DuplicateReceivable",
                   lambda: ch.tx(ch.contract.functions.registerUnit(
                       uid_of(dup), rkey_of(dup), wire.epoch(dup["invoice_date"]), wire.epoch(dup["due_date"]),
                       dup["amount_paise"], wire.party_key(dup["buyer_gstin"]), wire.party_key(dup["seller_gstin"])), "P2"),
                   [uid_of(hero)], [ctx.pid]) and same


def attack_double_pool(ctx):
    ch = ctx.ch
    pid2 = _open_pool(ctx)
    uid = ctx.by_label(ctx.truth["hero"])
    v = view(ch, uid)
    rh = ctx.rhash
    sig = engine.attest(ch.key["engine"], ch.chain_id, ch.contract.address, wire.hx(pid2), v, rh, 0)
    say(f"  {ctx.truth['hero']} is live in {POOL_NAME}; offered to demo-pool-02")
    return _attack(ch, "B adds a unit already live in Pool 1 to Pool 2", "AlreadyEncumbered",
                   lambda: ch.tx(ch.contract.functions.addToPool(pid2, uid, sig), "B"), [uid], [ctx.pid, pid2])


# --------------------------------------------------------------- lifecycle

def _shares(members, gm):
    """(buyer-group bps by key, seller bps by key) over live members."""
    total = sum(m["amountPaise"] for m in members) or 1
    g, sl = {}, {}
    for m in members:
        g[engine.group_of(m, gm)] = g.get(engine.group_of(m, gm), 0) + m["amountPaise"]
        sl[m["sellerKey"]] = sl.get(m["sellerKey"], 0) + m["amountPaise"]
    return ({k: v * 10_000 // total for k, v in g.items()}, {k: v * 10_000 // total for k, v in sl.items()})


def settle_some(ctx, limit=20):
    """Settle units whose removal keeps every concentration rule under 95% of its cap, taking units
    from the most concentrated groups and sellers first."""
    ch, live = ctx.ch, ctx.live()
    caps = {r["kind"]: r["value"] * 95 // 100 for r in ctx.rs["rules"] if r["kind"].endswith("_bps")}
    gcap = caps.get("buyer_group_concentration_max_bps", 10_000)
    scap = caps.get("seller_concentration_max_bps", 10_000)
    gs, ss = _shares(live, ctx.gm)
    order = sorted(live, key=lambda m: -max(gs[engine.group_of(m, ctx.gm)] / gcap, ss[m["sellerKey"]] / scap))
    done = 0
    for m in order:
        rest = [x for x in live if x is not m]
        if not rest or done >= limit:
            continue
        g2, s2 = _shares(rest, ctx.gm)
        if max(g2.values()) <= gcap and max(s2.values()) <= scap:
            reg = ch.names[ch.unit(wire.unhx(m["unitId"]))["registrar"]]
            ch.tx(ch.contract.functions.settle(wire.unhx(m["unitId"])), reg)
            live = rest
            done += 1
    say(f"  settled {done} units; live members {len(live)}, largest group {engine.largest_group_bps(live, ctx.gm)} bps")
    RESULT.update(settled=done, live=len(live), largestBps=engine.largest_group_bps(live, ctx.gm))
    return done


def _swap_candidates(ctx, largest_first=False):
    live, res, at = ctx.live(), ctx.reserve(), ctx.ch.ts() + 60
    for inn in sorted(res, key=lambda v: -v["amountPaise"] if largest_first else v["amountPaise"]):
        for out in sorted(live, key=lambda v: v["amountPaise"]):
            after = [m for m in live if m is not out] + [inn]
            yield out, inn, engine.evaluate(ctx.rs, inn, at, ctx.ev, after, ctx.gm)


def substitute_good(ctx):
    ch = ctx.ch
    for out, inn, fails in _swap_candidates(ctx):
        if not fails:
            version = ctx.version() + 1
            ch.tx(ch.contract.functions.substitute(ctx.pid, wire.unhx(out["unitId"]), wire.unhx(inn["unitId"]),
                                                   ctx.sign(inn, version)), "B")
            say(f"  substituted {label(ctx.rows[wire.unhx(out['unitId'])])} -> "
                f"{label(ctx.rows[wire.unhx(inn['unitId'])])}: manifest v{version}, all rules pass")
            RESULT.update(out=label(ctx.rows[wire.unhx(out["unitId"])]), into=label(ctx.rows[wire.unhx(inn["unitId"])]),
                          version=version)
            return True
    return False


def default_one(ctx):
    ch, live = ctx.ch, ctx.live()
    m = min(live, key=lambda v: v["dueDate"])
    uid = wire.unhx(m["unitId"])
    ch.warp(m["dueDate"] - ch.ts() + wire.DAY)
    reg = ch.names[ch.unit(uid)["registrar"]]
    ch.tx(ch.contract.functions.markDefault(uid), reg)
    say(f"  {label(ctx.rows[uid])} passed its due date and was marked DEFAULTED by {reg}")
    RESULT.update(unit=label(ctx.rows[uid]), registrar=reg)


def bad_swap(ctx, force=False):
    """A swap that would push one buyer group above the cap. The engine refuses to attest;
    with --force the engine key signs anyway (the compromised-key case) and the contract cannot tell."""
    ch = ctx.ch
    for out, inn, fails in _swap_candidates(ctx, largest_first=True):
        if fails and "buyer_group_concentration_max_bps" in {f["kind"] for f in fails}:
            say(f"  swap {label(ctx.rows[wire.unhx(out['unitId'])])} -> {label(ctx.rows[wire.unhx(inn['unitId'])])}")
            for f in fails:
                say(f"  ENGINE REFUSES: {f['rule']} {f['detail']}")
            LAST.clear()
            LAST.update(out=label(ctx.rows[wire.unhx(out["unitId"])]), into=label(ctx.rows[wire.unhx(inn["unitId"])]),
                        fails=[{"rule": f["rule"], "kind": f["kind"], "detail": f["detail"]} for f in fails],
                        forced=force, accepted=False)
            if not force:
                return True
            version = ctx.version() + 1
            ch.tx(ch.contract.functions.substitute(ctx.pid, wire.unhx(out["unitId"]), wire.unhx(inn["unitId"]),
                                                   ctx.sign(inn, version)), "B")
            LAST.update(accepted=True, version=version)
            say(f"  FORCED with the engine key: contract accepted it (manifest v{version}). Only the verifier can catch this.")
            return True
    say("  no rule-breaking swap candidate found")
    return False


def lifecycle(ctx):
    say("\nPOST-ISSUANCE LIFE")
    settle_some(ctx)
    if not substitute_good(ctx):
        say("  no clean substitution found")
    default_one(ctx)
