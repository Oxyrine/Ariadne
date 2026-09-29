from datetime import date

import pytest

from ariadne import evidence, generate


def scored(tmp_path, seed):
    generate.write(seed, tmp_path)
    units, ents, meta, truth = generate.load(tmp_path)
    ev, _ = evidence.score_units(units, ents, date.fromisoformat(meta["anchor"]))
    return {e["label"]: e for e in ev.values()}, ents, truth


def test_tarjan_finds_only_the_loop():
    adj = {"a": ["b"], "b": ["c"], "c": ["a", "d"], "d": []}
    assert sorted(map(sorted, evidence.tarjan_scc(adj))) == [["a", "b", "c"], ["d"]]


def test_cycle_search_is_bounded():
    n = evidence.MAX_CYCLE_LEN + 2
    adj = {str(i): [str((i + 1) % n)] for i in range(n)}
    cycles, _ = evidence.find_cycles(adj, sorted(adj))
    assert cycles == []  # the only cycle is longer than the bound


def test_seed_a_fabricated_loop_scores_high_and_legit_loop_low(tmp_path):
    by, _, truth = scored(tmp_path, "A")
    assert all(by[l]["evidenceScore"] > evidence.CUTOFF for l in truth["fabricated_loop"])
    assert all(by[l]["evidenceScore"] < evidence.CUTOFF for l in truth["legit_loop"])
    assert all(by[l]["reviewStatus"] == "PENDING_REVIEW" for l in truth["fabricated_loop"])
    assert set(by[truth["fabricated_loop"][0]]["features"]) == set(evidence.WEIGHTS)


def test_disguised_group_resolves_into_one_group(tmp_path):
    _, ents, truth = scored(tmp_path, "A")
    gm = evidence.resolve_groups(ents)
    members = [set(g["members"]) for g in gm["evidence"].values()]
    assert set(truth["groups"][-1]) in members or any(set(g) in members for g in truth["groups"])


def test_adversarial_seed_misses_are_real(tmp_path):
    by, ents, truth = scored(tmp_path, "C")
    assert all(by[l]["evidenceScore"] < evidence.CUTOFF for l in truth["fabricated_loop"])
    assert not evidence.resolve_groups(ents)["evidence"]  # a shared address alone must not auto-merge


def test_review_only_applies_to_held_units():
    with pytest.raises(ValueError):
        evidence.review({"u": {"label": "x", "reviewStatus": "NONE"}}, {"u": "EXCLUDED"})


def test_generator_is_deterministic_and_seed_sensitive(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    generate.write("A", a)
    generate.write("A", b)
    generate.write("B", c)
    assert (a / "units.csv").read_text() == (b / "units.csv").read_text()
    assert (a / "units.csv").read_text() != (c / "units.csv").read_text()
