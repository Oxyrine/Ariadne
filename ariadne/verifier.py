"""Layer 5: independent investor verifier.

Rebuilds a pool from chain events plus the signed rule set and frozen group map, re-runs the
eligibility engine itself, and trusts nothing the originator or Ariadne's backend reports.
The only inputs beyond the chain are pool.json (rules, group map, evidence), each checked
against the hashes pinned on-chain. Historical checks use state at the relevant block,
reconstructed from events, never current state.
"""
import json
from pathlib import Path

from eth_utils import keccak
from web3 import Web3

from . import engine, wire
from .chain import ROOT

OPNAMES = wire.OPS
STATE_ENTER = "FINANCED"  # a unit must be FINANCED to enter a pool


def _events(w3, contract):
    by_topic = {}
    for e in (x for x in contract.abi if x["type"] == "event"):
        sig = f"{e['name']}({','.join(i['type'] for i in e['inputs'])})"
        by_topic["0x" + keccak(sig.encode()).hex()] = getattr(contract.events, e["name"])()
    logs = w3.eth.get_logs({"fromBlock": 0, "toBlock": "latest", "address": contract.address})
    out = []
    for lg in sorted(logs, key=lambda l: (l["blockNumber"], l["logIndex"])):
        ev = by_topic.get("0x" + bytes(lg["topics"][0]).hex())
        if ev:
            d = ev.process_log(lg)
            out.append((d["event"], dict(d["args"]), d["blockNumber"]))
    return out


def _view(u, uid):
    return {"unitId": wire.hx(uid), "receivableKey": wire.hx(u["receivableKey"]), "buyerKey": wire.hx(u["buyerKey"]),
            "sellerKey": wire.hx(u["sellerKey"]), "state": STATE_ENTER, "owner": u["owner"],
            "invoiceDate": u["invoiceDate"], "dueDate": u["dueDate"], "amountPaise": u["amountPaise"],
            "transferCount": u["transferCount"]}


def verify(pool_path: Path, rpc: str):
    doc = json.loads(Path(pool_path).read_text())
    w3 = Web3(Web3.HTTPProvider(rpc))
    abi = json.loads((ROOT / "out/AriadneLedger.sol/AriadneLedger.json").read_text())["abi"]
    addr = Web3.to_checksum_address(doc["ledger"])
    if not w3.eth.get_code(addr):
        raise SystemExit(f"no contract at {addr} on {rpc}")
    ledger = w3.eth.contract(address=addr, abi=abi)
    pid = wire.unhx(doc["poolId"])
    rs, gm, evdoc = doc["rules"], doc["groupMap"], doc["evidence"]
    ev = evdoc["units"]
    lab = lambda uid: ev.get(wire.hx(uid), {}).get("label", wire.hx(uid)[:12])
    ts_cache = {}
    ts = lambda b: ts_cache.setdefault(b, w3.eth.get_block(b).timestamp)

    checks, failures, timeline = {}, [], []

    def result(name, ok, detail="", info=True):
        checks[name] = {"status": "PASS" if ok else "FAIL" if info else "INFO", "detail": detail}

    # --- pinned inputs
    pool = ledger.functions.pools(pid).call()
    originator, p_rules, p_groups, p_evidence, p_commit, p_members, p_version, p_sealed = pool
    try:
        signer = engine.ruleset_signer(rs)
        engine.validate_ruleset(rs)
        rules_ok = wire.rules_hash(rs) == p_rules
        result("RULES_PINNED", rules_ok, f"signed by {signer[:10]}…" if rules_ok else "rule set does not hash to the pinned rulesHash")
    except Exception as e:
        rules_ok = False
        result("RULES_PINNED", False, str(e))
    result("GROUP_MAP_PINNED", wire.content_hash(gm) == p_groups)
    result("EVIDENCE_PINNED", wire.content_hash(evdoc) == p_evidence)

    # --- replay
    units, live_pool, members, deferred = {}, {}, {}, []
    commit = wire.ZERO32
    n_events = commit_bad = 0
    own_total = own_bad = uniq_bad = unit_total = unit_bad = att_total = att_bad = 0
    versions, ever, settled, defaulted, sealed_at = {}, set(), [], [], None

    def flag(check, uid, fails, block):
        for f in fails:
            failures.append({"check": check, "unit": "(pool)" if uid == wire.ZERO32 else lab(uid), "rule": f["rule"], "kind": f["kind"],
                             "detail": f["detail"], "block": block})

    def attest_check(uid, view, block, after):
        nonlocal att_total, att_bad
        att_total += 1
        fails = engine.evaluate(rs, view, ts(block), ev, after, gm)
        if fails:
            att_bad += 1
            flag("ATTESTATIONS", uid, fails, block)

    for name, a, block in _events(w3, ledger):
        if name == "UnitRegistered":
            units[a["unitId"]] = {**{k: a[k] for k in ("receivableKey", "registrar", "invoiceDate", "dueDate",
                                                       "amountPaise", "buyerKey", "sellerKey")},
                                  "owner": None, "transferCount": 0}
        elif name == "UnitFinanced":
            units[a["unitId"]]["owner"] = a["financier"]
        elif name == "UnitTransferred":
            units[a["unitId"]]["owner"], units[a["unitId"]]["transferCount"] = a["to"], a["transferCount"]
        elif name == "MembershipChanged":
            uid, op, poolid = a["unitId"], OPNAMES[a["op"]], a["poolId"]
            u = units[uid]
            entering = op in ("ADD", "SUBSTITUTE_IN")
            if entering and live_pool.get(u["receivableKey"]) not in (None, poolid):
                uniq_bad += 1
                failures.append({"check": "RECEIVABLE_UNIQUE", "unit": lab(uid), "rule": "-", "kind": "-",
                                 "detail": "receivable live in two pools", "block": block})
            members.setdefault(poolid, {})
            if entering:
                live_pool[u["receivableKey"]] = poolid
                members[poolid][uid] = _view(u, uid)
            else:
                live_pool.pop(u["receivableKey"], None)
                members[poolid].pop(uid, None)
            if poolid != pid:
                continue
            n_events += 1
            commit = wire.next_commitment(commit, a["op"], uid)
            if commit != a["commitment"]:
                commit_bad += 1
            timeline.append({"block": block, "ts": ts(block), "op": op, "unit": lab(uid), "version": a["manifestVersion"]})
            if entering:
                ever.add(uid)
                own_total += 1
                if u["owner"] != originator:
                    own_bad += 1
                    failures.append({"check": "OWNERSHIP", "unit": lab(uid), "rule": "-", "kind": "-",
                                     "detail": "originator did not own the unit", "block": block})
                unit_total += 1
                uf = engine.check_unit(rs, _view(u, uid), ts(block), ev)
                if uf:
                    unit_bad += 1
                    flag("UNIT_RULES", uid, uf, block)
                if op == "ADD":
                    deferred.append((uid, block))  # pre-seal: attested against the proposed sealed pool
                else:
                    after = list(members[pid].values())
                    attest_check(uid, _view(u, uid), block, after)
                    pf = engine.check_pool(rs, after, gm)
                    versions[a["manifestVersion"]] = (block, pf, engine.largest_group_bps(after, gm))
                    flag("POOL_RULES", uid, pf, block)
        elif name == "PoolSealed" and a["poolId"] == pid:
            sealed_at = block
            after = list(members[pid].values())
            for uid, b in deferred:
                if uid in members[pid]:
                    attest_check(uid, members[pid][uid], b, after)
            pf = engine.check_pool(rs, after, gm)
            versions[1] = (block, pf, engine.largest_group_bps(after, gm))
            flag("POOL_RULES", b"\0" * 32, pf, block)
        elif name in ("UnitSettled", "UnitDefaulted") and a["unitId"] in ever:
            (settled if name == "UnitSettled" else defaulted).append(a["unitId"])

    result("COMMITMENT_REPLAY", commit_bad == 0 and commit == p_commit,
           f"{n_events} events" if commit == p_commit else "replay differs from the contract's commitment")
    result("OWNERSHIP", own_bad == 0, f"{own_total - own_bad}/{own_total}")
    result("RECEIVABLE_UNIQUE", uniq_bad == 0, f"{own_total - uniq_bad}/{own_total}")
    result("UNIT_RULES", unit_bad == 0, f"{unit_total - unit_bad}/{unit_total}")
    bad_v = sorted(v for v, (_, pf, _) in versions.items() if pf)
    top = max((bps for _, _, bps in versions.values()), default=0)
    result("POOL_RULES", not bad_v, f"{len(versions)} versions, largest buyer group {top / 100:.1f}%"
           if not bad_v else "broken at manifest " + ", ".join(f"v{v}" for v in bad_v))
    result("ATTESTATIONS", att_bad == 0, f"{att_total - att_bad}/{att_total} agree")
    amt = lambda ids: sum(units[i]["amountPaise"] for i in ids)
    out = list(members.get(pid, {}))
    perf = {"settled": len(settled), "defaulted": len(defaulted), "outstanding": len(out),
            "settledPaise": amt(settled), "defaultedPaise": amt(defaulted), "outstandingPaise": amt(out)}
    result("PERFORMANCE", True,
           f"settled {perf['settled']} · defaulted {perf['defaulted']} · outstanding {perf['outstanding']}", info=False)
    hard = [c for c, r in checks.items() if r["status"] == "FAIL"]
    return {"pool": rs["ruleSetId"], "version": p_version, "ledger": addr, "sealed": p_sealed,
            "checks": checks, "failures": failures, "timeline": timeline, "performance": perf,
            "status": "VERIFIED" if not hard else "NOT VERIFIED", "failed": hard,
            "events": n_events}


def format_result(r) -> str:
    lines = [f"POOL                {r['pool']}  (manifest v{r['version']})  SYNTHETIC DATA"]
    for name, c in r["checks"].items():
        lines.append(f"{name:<19} {c['status']}" + (f"  ({c['detail']})" if c["detail"] else ""))
    for f in r["failures"]:
        rule = f"{f['rule']} {f['kind']} ({f['detail']})" if f["rule"] != "-" else f["detail"]
        lines.append(f"  {f['check']} DISAGREE unit {f['unit']} {rule} at block {f['block']}"
                     if f["check"] == "ATTESTATIONS" else
                     f"  {f['check']} unit {f['unit']} {rule} at block {f['block']}")
    lines += ["", f"FINAL STATUS: {r['status']}" + (f"  ({', '.join(r['failed'])})" if r["failed"] else "")]
    return "\n".join(lines)


def trace_events(ledger, w3, uid) -> list:
    """One unit's events as plain dicts (used by the web UI)."""
    out = []
    for name, a, block in _events(w3, ledger):
        if a.get("unitId") != uid:
            continue
        e = {"event": name, "block": block, "ts": w3.eth.get_block(block).timestamp}
        e.update({k: (wire.hx(v) if isinstance(v, (bytes, bytearray)) else v)
                  for k, v in a.items() if k not in ("unitId", "buyerKey", "sellerKey", "receivableKey")})
        if name == "MembershipChanged":
            e["op"] = OPNAMES[a["op"]]
        out.append(e)
    return out


def trace(pool_path: Path, rpc: str, unit_label: str) -> str:
    """One unit's journey from chain events (Act 1 of the demo)."""
    doc = json.loads(Path(pool_path).read_text())
    w3 = Web3(Web3.HTTPProvider(rpc))
    abi = json.loads((ROOT / "out/AriadneLedger.sol/AriadneLedger.json").read_text())["abi"]
    ledger = w3.eth.contract(address=Web3.to_checksum_address(doc["ledger"]), abi=abi)
    uid = next(wire.unhx(u) for u, e in doc["evidence"]["units"].items() if e["label"] == unit_label)
    names = {}
    lines = [f"{unit_label}  ({wire.hx(uid)[:14]}…)"]
    for name, a, block in _events(w3, ledger):
        if a.get("unitId") != uid:
            continue
        if name == "UnitRegistered":
            lines.append(f"  block {block}: REGISTERED on platform {a['registrar'][:8]}…, ₹{a['amountPaise'] / 100:,.2f}")
        elif name == "UnitFinanced":
            lines.append(f"  block {block}: FINANCED by {a['financier'][:8]}…")
        elif name == "UnitTransferred":
            lines.append(f"  block {block}: RE-DISCOUNTED {a['from'][:8]}… -> {a['to'][:8]}… (transfer #{a['transferCount']})")
        elif name == "MembershipChanged":
            lines.append(f"  block {block}: {OPNAMES[a['op']]} in pool {wire.hx(a['poolId'])[:10]}… (manifest v{a['manifestVersion']})")
        elif name in ("UnitSettled", "UnitDefaulted"):
            lines.append(f"  block {block}: {name[4:].upper()}")
    return "\n".join(lines)
