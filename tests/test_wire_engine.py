import pytest
from eth_account import Account

from ariadne import engine, wire

DAY = wire.DAY
NOW = 1_790_640_000


def unit(**kw):
    u = {"unitId": "0x01", "receivableKey": "0xaa", "buyerKey": "0xb1", "sellerKey": "0x51", "state": "FINANCED",
         "owner": "0xO", "invoiceDate": NOW - 30 * DAY, "dueDate": NOW + 60 * DAY, "amountPaise": 1000,
         "transferCount": 0}
    return {**u, **kw}


def rules(*rs):
    return {"ruleSetId": "t", "version": 1,
            "rules": [{"id": f"R{i}", "kind": k, "value": v} for i, (k, v) in enumerate(rs, 1)]}


EV = {"0x01": {"evidenceScore": 0, "reviewStatus": "NONE", "label": "x"}}


def failed(rs, u, ev=EV):
    return [f["kind"] for f in engine.check_unit(rs, u, NOW, ev)]


def test_state_required():
    rs = rules(("state_required", "FINANCED"))
    assert not failed(rs, unit())
    assert failed(rs, unit(state="POOLED")) == ["state_required"]


def test_tenor_max_days():
    rs = rules(("tenor_max_days", 90))
    assert not failed(rs, unit(invoiceDate=NOW - 30 * DAY, dueDate=NOW + 60 * DAY))
    assert failed(rs, unit(invoiceDate=NOW - 30 * DAY, dueDate=NOW + 61 * DAY))


def test_min_days_to_maturity_uses_gating_time_not_now():
    rs = rules(("min_days_to_maturity", 15))
    u = unit(dueDate=NOW + 16 * DAY)
    assert not engine.check_unit(rs, u, NOW, EV)
    assert engine.check_unit(rs, u, NOW + 2 * DAY, EV)  # same unit, later block, now too close


def test_max_transfer_count():
    rs = rules(("max_transfer_count", 2))
    assert not failed(rs, unit(transferCount=2))
    assert failed(rs, unit(transferCount=3))


@pytest.mark.parametrize("score,status,bad", [(500, "NONE", False), (900, "PENDING_REVIEW", True),
                                              (900, "EXCLUDED", True), (900, "CLEARED", False)])
def test_evidence_score_max(score, status, bad):
    rs = rules(("evidence_score_max", 700))
    ev = {"0x01": {"evidenceScore": score, "reviewStatus": status}}
    assert bool(failed(rs, unit(), ev)) == bad


def test_unit_missing_from_evidence_set_fails():
    assert failed(rules(("evidence_score_max", 700)), unit(), {}) == ["evidence_score_max"]


def test_buyer_group_concentration_uses_resolved_group():
    rs = rules(("buyer_group_concentration_max_bps", 1000))
    ms = [unit(unitId=f"0x{i}", buyerKey=f"0xb{i}", amountPaise=100) for i in range(10)]
    assert not engine.check_pool(rs, ms, {"groups": {}})  # ten buyers at 10% each: at the cap, not over
    merged = {"groups": {"0xb0": "g", "0xb1": "g"}}  # two of them are one group: 20%
    f = engine.check_pool(rs, ms, merged)
    assert len(f) == 1 and len(f[0]["units"]) == 2


def test_seller_concentration_and_min_pool_size():
    ms = [unit(unitId=f"0x{i}", sellerKey="0xs", amountPaise=100) for i in range(4)]
    assert engine.check_pool(rules(("seller_concentration_max_bps", 500)), ms, {"groups": {}})
    assert engine.check_pool(rules(("min_pool_size", 5)), ms, {"groups": {}}, sealing=True)
    assert not engine.check_pool(rules(("min_pool_size", 4)), ms, {"groups": {}}, sealing=True)
    # a closing condition only: later settlements shrinking the pool must not break it
    assert not engine.check_pool(rules(("min_pool_size", 5)), ms, {"groups": {}})


def test_unsupported_kind_is_refused():
    with pytest.raises(ValueError):
        engine.validate_ruleset(rules(("made_up_kind", 1)))


@pytest.mark.parametrize("grade,bad", [("AAA", False), ("A-", False), ("BBB+", True), (None, True)])
def test_buyer_rating_min(grade, bad):
    ev = {"0x01": {"evidenceScore": 0, "reviewStatus": "NONE", "buyerRating": grade}}
    assert bool(failed(rules(("buyer_rating_min", "A-")), unit(), ev)) == bad


def test_ruleset_signature_roundtrip_and_tamper():
    key = Account.create().key
    rs = engine.sign_ruleset(rules(("tenor_max_days", 90)), key)
    assert engine.ruleset_signer(rs) == Account.from_key(key).address
    rs["rules"][0]["value"] = 365
    with pytest.raises(ValueError):
        engine.ruleset_signer(rs)


def test_receivable_key_ignores_formatting_but_not_substance():
    a = wire.receivable_key("33AAAPL1234C1Z5", "29BBBCM5678D1Z2", "INV-04418", "2026-08-02", 48500000)
    b = wire.receivable_key(" 33aaapl1234c1z5", "29BBBCM5678D1Z2 ", " inv-4418 ", "2026-08-02", 48500000)
    assert a == b
    assert a != wire.receivable_key("33AAAPL1234C1Z5", "29BBBCM5678D1Z2", "INV-04418", "2026-08-02", 48500001)


def test_canonical_json_is_order_independent_and_rejects_floats():
    assert wire.content_hash({"a": 1, "b": [1, 2]}) == wire.content_hash({"b": [1, 2], "a": 1})
    with pytest.raises(ValueError):
        wire.content_hash({"a": 1.5})


def test_rules_hash_ignores_signature():
    rs = rules(("tenor_max_days", 90))
    assert wire.rules_hash(rs) == wire.rules_hash({**rs, "signature": "0x1", "signedBy": "0x2"})


def test_commitment_chain_is_order_sensitive():
    a, b = b"\x01" * 32, b"\x02" * 32
    c1 = wire.next_commitment(wire.next_commitment(wire.ZERO32, 0, a), 0, b)
    c2 = wire.next_commitment(wire.next_commitment(wire.ZERO32, 0, b), 0, a)
    assert c1 != c2
