"""Detection metrics against truth.json on seeds A, B, C, plus the loop-enumeration benchmark.
Everything here is measured from the generator's synthetic data, never hand-entered."""
import random
import time
from datetime import date
from itertools import combinations
from pathlib import Path

from . import evidence, generate
from .chain import ROOT


def _pairs(groups):
    return {frozenset(p) for g in groups for p in combinations(sorted(g), 2)}


def detect(seed: str):
    d = ROOT / "runs" / seed
    generate.write(seed, d)
    units, ents, meta, truth = generate.load(d)
    ev, diag = evidence.score_units(units, ents, date.fromisoformat(meta["anchor"]))
    by_label = {e["label"]: e for e in ev.values()}
    hit = lambda labs: sum(by_label[l]["evidenceScore"] > evidence.CUTOFF for l in labs)
    fab, leg = truth["fabricated_loop"], truth["legit_loop"]
    gm = evidence.resolve_groups(ents)
    pred = _pairs(g["members"] for g in gm["evidence"].values())
    true = _pairs(truth["groups"])
    tp = len(pred & true)
    return {"seed": seed, "units": len(units),
            "fab_recall": f"{hit(fab)}/{len(fab)}", "legit_fp": f"{hit(leg)}/{len(leg)}",
            "er_precision": f"{tp}/{len(pred)}" if pred else "n/a (no merges)",
            "er_recall": f"{tp}/{len(true)}" if true else "n/a",
            "fab_scores": sorted({by_label[l]['evidenceScore'] for l in fab}),
            "legit_scores": sorted({by_label[l]['evidenceScore'] for l in leg})}


def benchmark(n=30, probs=(0.03, 0.06, 0.10, 0.15, 0.25), seed=1):
    """Runtime of SCC + bounded cycle search as one component gets denser."""
    rows = []
    for p in probs:
        rng = random.Random(seed)
        nodes = [f"N{i:03d}" for i in range(n)]
        adj = {v: set() for v in nodes}
        for i in range(n):  # a ring keeps it strongly connected
            adj[nodes[i]].add(nodes[(i + 1) % n])
        for a in nodes:
            for b in nodes:
                if a != b and rng.random() < p:
                    adj[a].add(b)
        adj = {k: sorted(v) for k, v in adj.items()}
        t = time.perf_counter()
        comps = evidence.tarjan_scc(adj)
        cycles, trunc = 0, False
        for c in comps:
            if len(c) > 1:
                cs, tr = evidence.find_cycles(adj, c)
                cycles, trunc = cycles + len(cs), trunc or tr
        rows.append({"edges": sum(map(len, adj.values())), "cycles": cycles, "truncated": trunc,
                     "ms": round((time.perf_counter() - t) * 1000, 1)})
    return rows


def run():
    lines = ["# Detection metrics (synthetic data, measured)", "",
             f"Cut-off {evidence.CUTOFF}. Weights are hand-set, not trained.", "",
             "| Metric | Seed A (demo) | Seed B (unseen) | Seed C (adversarial) |", "| --- | --- | --- | --- |"]
    r = {s: detect(s) for s in "ABC"}
    for key, name in [("fab_recall", "Fabricated-loop recall (units above cut-off)"),
                      ("legit_fp", "Legitimate-loop false positives"),
                      ("er_precision", "Entity-resolution precision (pairs)"),
                      ("er_recall", "Entity-resolution recall (pairs)"),
                      ("fab_scores", "Fabricated-loop score(s)"), ("legit_scores", "Legitimate-loop score(s)")]:
        lines.append(f"| {name} | " + " | ".join(str(r[s][key]) for s in "ABC") + " |")
    lines += ["", f"# Loop-enumeration benchmark (30-node strongly connected component, cycle length <= {evidence.MAX_CYCLE_LEN},"
              f" cap {evidence.MAX_CYCLES_PER_SCC} cycles)", "",
              "| Edges | Cycles found | Truncated | Runtime (ms) |", "| --- | --- | --- | --- |"]
    lines += [f"| {b['edges']} | {b['cycles']} | {b['truncated']} | {b['ms']} |" for b in benchmark()]
    out = "\n".join(lines)
    (ROOT / "runs" / "metrics.md").write_text(out, encoding="utf-8")
    return out
