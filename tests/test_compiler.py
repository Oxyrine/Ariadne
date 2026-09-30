import pytest
from eth_account import Account

from ariadne import compiler, engine, originator

TEXT = compiler.sample_text()
KINDS = [r["kind"] for r in originator.RULES["rules"]]


def test_fixture_maps_to_the_nine_rule_vocabulary_in_order():
    out = compiler.parse(TEXT)
    assert [r["kind"] for r in out["rules"]] == KINDS
    assert [r["value"] for r in out["rules"]] == [r["value"] for r in originator.RULES["rules"]]
    assert out["aiUsed"] is False


def test_the_servicer_clause_is_unsupported_not_dropped():
    out = compiler.parse(TEXT)
    assert len(out["unsupported"]) == 1 and "servicer" in out["unsupported"][0]["span"]
    assert len(out["rules"]) + len(out["unsupported"]) == len(compiler.clauses(TEXT))


def test_every_rule_carries_source_words_verbatim():
    for r in compiler.parse(TEXT)["rules"]:
        assert r["sourceSpan"] in TEXT


def test_percent_becomes_basis_points_and_group_basis_is_resolved():
    r = {x["kind"]: x for x in compiler.parse(TEXT)["rules"]}
    assert r["buyer_group_concentration_max_bps"]["value"] == 1000
    assert r["buyer_group_concentration_max_bps"]["basis"] == "resolved_group"
    assert r["seller_concentration_max_bps"]["value"] == 500


def test_ambiguities_are_flagged_for_the_reviewer():
    r = {x["kind"]: x for x in compiler.parse(TEXT)["rules"]}
    assert r["buyer_group_concentration_max_bps"]["ambiguities"]  # 'exposure'
    assert any("fraud verdict" in a for a in r["evidence_score_max"]["ambiguities"])


def test_out_of_bounds_and_fractional_bps_go_to_unsupported():
    out = compiler.parse("Maximum invoice tenor is 900 days.\nNo resolved buyer group may exceed 10.005 percent.")
    assert not out["rules"] and len(out["unsupported"]) == 2


def test_reviewer_cannot_smuggle_in_words_or_values_the_document_lacks():
    rules = compiler.parse(TEXT)["rules"]
    rules[0]["status"] = "APPROVED"
    rules[0]["sourceSpan"] = "Units may be anything."
    with pytest.raises(ValueError, match="source words"):
        compiler.to_ruleset(rules, [], TEXT, "t")
    rules[0]["sourceSpan"] = compiler.parse(TEXT)["rules"][0]["sourceSpan"]
    rules[0]["value"] = "SETTLED"
    with pytest.raises(ValueError, match="bounds"):
        compiler.to_ruleset(rules, [], TEXT, "t")


def test_only_approved_rules_reach_the_signed_set_and_the_engine_accepts_it():
    out = compiler.parse(TEXT)
    for r in out["rules"]:
        r["status"] = "APPROVED"
    out["rules"][2]["status"] = "REJECTED"
    rs = compiler.to_ruleset(out["rules"], out["unsupported"], TEXT, "t", signed_at="2026-09-30T00:00:00Z")
    assert len(rs["rules"]) == 8 and rs["unsupported"]
    signed = engine.sign_ruleset(rs, Account.create().key)
    engine.validate_ruleset(signed)
    assert engine.ruleset_signer(signed)
