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
        {"id": "R4", "kind": "evidence_score_max", "value": 700},
        {"id": "R5", "kind": "buyer_group_concentration_max_bps", "value": 1000, "basis": "resolved_group"},
    ],
    "sourceClauses": {"R2": "4.2(a)", "R5": "4.3(b)"},
    "unsupported": [],
}


def uid_of(r): return wire.unit_id(r["platform"], r["unit_no"])
def label(r): return f"{r['platform']}:{r['unit_no']}"


def rkey_of(r):
    return wire.receivable_key(r["seller_gstin"], r["buyer_gstin"], r["invoice_no"], r["invoice_date"], r["amount_paise"])


def say(*a): print(*a, flush=True)


# ----------------------------------------------------------------- replay

def setup(ch, units):
    """Register, finance and re-discount every unit on the chain (the synthetic registrars' history)."""
    f = ch.contract.functions
    for r in units:
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

def review_evidence(ev):
    """The human review step. The demo reviewer excludes every held unit; each is listed."""
    held = {uid: "EXCLUDED" for uid, e in ev.items() if e["reviewStatus"] == "PENDING_REVIEW"}
    for uid in held:
        e = ev[uid]
        say(f"  review: {e['label']} {e['cycleId']} score {e['evidenceScore']} {e['features']} -> EXCLUDED")
    return evidence.review(ev, held)


def build_pool(ch, seed_dir: Path, name=POOL_NAME):
    units, ents, meta, truth = generate.load(seed_dir)
    ev, _ = evidence.score_units(units, ents, date.fromisoformat(meta["anchor"]))
    say("Evidence scoring:")
    ev = review_evidence(ev)
    evdoc = evidence.evidence_doc(ev)
    gm = evidence.resolve_groups(ents)
    for gid, g in gm["evidence"].items():
        say(f"  group {gid}: {len(g['members'])} GSTINs  links: {g['links']}")
    rs = {**RULES, "ruleSetId": name}
    rs = engine.sign_ruleset(rs, ch.key["rules"])
    engine.validate_ruleset(rs)
    assert engine.ruleset_signer(rs)
    pid, rhash = wire.pool_id(name), wire.rules_hash(rs)
    ch.tx(ch.contract.functions.createPool(pid, rhash, wire.content_hash(gm), wire.content_hash(evdoc)), "B")

    at = ch.ts() + 60
    rows = {uid_of(r): r for r in units}
    cands = []
    for uid, r in rows.items():
        v = view(ch, uid)
        if r["reserve"] or v["owner"] != ch.acct["B"]:
            continue
        if not engine.check_unit(rs, v, at, ev):
            cands.append(v)
    say(f"Eligible on unit rules: {len(cands)} of {len(rows)} units")

    members = list(cands)
    while True:  # trim the largest resolved group down to TARGET_BPS, smallest units first
        total = sum(m["amountPaise"] for m in members)
        by = {}
        for m in members:
            by.setdefault(engine.group_of(m, gm), []).append(m)
        g, ms = max(by.items(), key=lambda kv: sum(m["amountPaise"] for m in kv[1]))
        if sum(m["amountPaise"] for m in ms) * 10_000 // total <= TARGET_BPS:
            break
        members.remove(min(ms, key=lambda m: m["amountPaise"]))
    say(f"After concentration trim: {len(members)} units, largest group "
        f"{engine.largest_group_bps(members, gm)} bps")
    assert not engine.check_pool(rs, members, gm), "trimmed pool still breaks a pool rule"

    ledger = ch.contract.address
    for m in members:
        fails = engine.evaluate(rs, m, at, ev, members, gm)
        assert not fails, fails
        sig = engine.attest(ch.key["engine"], ch.chain_id, ledger, wire.hx(pid), m, wire.hx(rhash), 0)
        ch.tx(ch.contract.functions.addToPool(pid, wire.unhx(m["unitId"]), sig), "B")
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
    try:
        action()
        say("  !! NOT REJECTED")
        return False
    except Exception as e:
        name = getattr(e, "name", None)
        say(f"  reverted: {e}")
        after = ch.snapshot(uids, pids)
        ok = name == expect and before == after
        say(f"  state before == after: {before == after}   expected {expect}: {name == expect}")
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

def settle_some(ctx, limit=20):
    ch, live = ctx.ch, ctx.live()
    by = {}
    for m in live:
        by.setdefault(engine.group_of(m, ctx.gm), []).append(m)
    order = [m for g in sorted(by, key=lambda g: -sum(x["amountPaise"] for x in by[g])) for m in by[g]]
    done = 0
    for m in order:
        rest = [x for x in live if x is not m]
        if done < limit and rest and engine.largest_group_bps(rest, ctx.gm) <= SETTLE_BPS:
            reg = ch.names[ch.unit(wire.unhx(m["unitId"]))["registrar"]]
            ch.tx(ch.contract.functions.settle(wire.unhx(m["unitId"])), reg)
            live = rest
            done += 1
    say(f"  settled {done} units; live members {len(live)}, largest group {engine.largest_group_bps(live, ctx.gm)} bps")
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


def bad_swap(ctx, force=False):
    """A swap that would push one buyer group above the cap. The engine refuses to attest;
    with --force the engine key signs anyway (the compromised-key case) and the contract cannot tell."""
    ch = ctx.ch
    for out, inn, fails in _swap_candidates(ctx, largest_first=True):
        if fails and {f["rule"] for f in fails} == {"R5"}:
            say(f"  swap {label(ctx.rows[wire.unhx(out['unitId'])])} -> {label(ctx.rows[wire.unhx(inn['unitId'])])}")
            for f in fails:
                say(f"  ENGINE REFUSES: {f['rule']} {f['detail']}")
            if not force:
                return True
            version = ctx.version() + 1
            ch.tx(ch.contract.functions.substitute(ctx.pid, wire.unhx(out["unitId"]), wire.unhx(inn["unitId"]),
                                                   ctx.sign(inn, version)), "B")
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
