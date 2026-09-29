"""Layer 2: eligibility engine. Pure functions over a signed rule set, a unit view,
the frozen buyer-group map and the scored evidence set. The verifier imports the
same functions, so the engine and the verifier cannot disagree by construction
unless an attestation was forged or the inputs differ.

A unit view is a dict: unitId, receivableKey, buyerKey, sellerKey (hex strings),
state (str), owner, invoiceDate, dueDate (epoch seconds), amountPaise, transferCount.
"""
from eth_account import Account
from eth_account.messages import encode_typed_data

from . import wire

UNIT_KINDS = {"state_required", "tenor_max_days", "min_days_to_maturity", "max_transfer_count",
              "evidence_score_max"}
POOL_KINDS = {"buyer_group_concentration_max_bps", "seller_concentration_max_bps", "min_pool_size"}
# In the closed vocabulary but needs rating data the prototype does not have.
UNIMPLEMENTED = {"buyer_rating_min"}


def validate_ruleset(ruleset: dict) -> None:
    ids = set()
    for r in ruleset["rules"]:
        if r["kind"] not in UNIT_KINDS | POOL_KINDS:
            raise ValueError(f"rule {r['id']}: kind {r['kind']!r} is not supported by this engine")
        if r["id"] in ids:
            raise ValueError(f"duplicate rule id {r['id']}")
        ids.add(r["id"])


def ruleset_signer(ruleset: dict) -> str:
    """Recover who signed the rule set; raises if the signature does not match signedBy."""
    msg = encode_typed_data(full_message=wire.ruleset_typed(ruleset))
    signer = Account.recover_message(msg, signature=wire.unhx(ruleset["signature"]))
    if signer.lower() != ruleset["signedBy"].lower():
        raise ValueError(f"rule set signed by {signer}, not {ruleset['signedBy']}")
    return signer


def sign_ruleset(ruleset: dict, key) -> dict:
    ruleset = {k: v for k, v in ruleset.items() if k not in ("signedBy", "signature")}
    acct = Account.from_key(key)
    sig = acct.sign_message(encode_typed_data(full_message=wire.ruleset_typed(ruleset))).signature
    return {**ruleset, "signedBy": acct.address, "signature": wire.hx(sig)}


def _fail(rule, detail, units=()):
    return {"rule": rule["id"], "kind": rule["kind"], "detail": detail, "units": list(units)}


def check_unit(ruleset: dict, unit: dict, at_ts: int, evidence: dict) -> list:
    """Unit rules, evaluated against the unit's state at the gating block (at_ts)."""
    fails = []
    for r in ruleset["rules"]:
        k, v = r["kind"], r["value"]
        if k == "state_required":
            if unit["state"] != v:
                fails.append(_fail(r, f"state {unit['state']}, required {v}"))
        elif k == "tenor_max_days":
            tenor = (unit["dueDate"] - unit["invoiceDate"]) // wire.DAY
            if tenor > v:
                fails.append(_fail(r, f"tenor {tenor} days > {v}"))
        elif k == "min_days_to_maturity":
            left = (unit["dueDate"] - at_ts) // wire.DAY
            if left < v:
                fails.append(_fail(r, f"{left} days to maturity < {v}"))
        elif k == "max_transfer_count":
            if unit["transferCount"] > v:
                fails.append(_fail(r, f"re-discounted {unit['transferCount']} times > {v}"))
        elif k == "evidence_score_max":
            e = evidence.get(unit["unitId"])
            if e is None:
                fails.append(_fail(r, "unit not in the scored evidence set"))
            elif e["reviewStatus"] == "EXCLUDED":
                fails.append(_fail(r, f"excluded by reviewer (score {e['evidenceScore']})"))
            elif e["evidenceScore"] > v and e["reviewStatus"] != "CLEARED":
                fails.append(_fail(r, f"evidence score {e['evidenceScore']} > {v}, {e['reviewStatus']}"))
    return fails


def _concentration(rule, members, key_of, label):
    total = sum(m["amountPaise"] for m in members)
    by = {}
    for m in members:
        by.setdefault(key_of(m), []).append(m)
    fails = []
    for key, ms in sorted(by.items()):
        amt = sum(m["amountPaise"] for m in ms)
        if amt * 10_000 > rule["value"] * total:
            bps = amt * 10_000 // total
            fails.append(_fail(rule, f"{label} {key[:12]} at {bps:,} bps > {rule['value']:,}",
                               [m["unitId"] for m in ms]))
    return fails


def largest_group_bps(members: list, group_map: dict) -> int:
    total = sum(m["amountPaise"] for m in members)
    by = {}
    for m in members:
        g = group_of(m, group_map)
        by[g] = by.get(g, 0) + m["amountPaise"]
    return max(by.values()) * 10_000 // total if total else 0


def group_of(unit: dict, group_map: dict) -> str:
    """Resolved buyer group; a buyer not in the frozen map is its own group."""
    return group_map["groups"].get(unit["buyerKey"], unit["buyerKey"])


def check_pool(ruleset: dict, members: list, group_map: dict) -> list:
    """Pool rules over the live members of one manifest version."""
    fails = []
    for r in ruleset["rules"]:
        k = r["kind"]
        if k == "buyer_group_concentration_max_bps":
            fails += _concentration(r, members, lambda m: group_of(m, group_map), "buyer group")
        elif k == "seller_concentration_max_bps":
            fails += _concentration(r, members, lambda m: m["sellerKey"], "seller")
        elif k == "min_pool_size" and len(members) < r["value"]:
            fails.append(_fail(r, f"{len(members)} units < {r['value']}"))
    return fails


def evaluate(ruleset, unit, at_ts, evidence, pool_after, group_map) -> list:
    """Every failure that should stop an attestation for `unit` entering a pool whose
    live membership afterwards would be `pool_after`. Pool failures are kept only if
    they involve this unit, so the verdict is about this unit's entry."""
    fails = check_unit(ruleset, unit, at_ts, evidence)
    fails += [f for f in check_pool(ruleset, pool_after, group_map)
              if not f["units"] or unit["unitId"] in f["units"]]
    return fails


def attest(engine_key, chain_id, ledger, pool_id, unit, rhash, version) -> bytes:
    typed = wire.attestation_typed(chain_id, ledger, pool_id, unit["unitId"], unit["receivableKey"],
                                   rhash, version)
    return Account.sign_message(encode_typed_data(full_message=typed), engine_key).signature
