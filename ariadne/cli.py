"""ariadne CLI. `python -m ariadne demo --seed A` runs the whole story against a local Anvil chain.
Every number printed comes from the running system; all data is synthetic."""
import argparse
import json
from datetime import date
from pathlib import Path

from . import chain, generate, originator, report, verifier, wire
from .chain import ROOT

RUNS = ROOT / "runs"


def say(*a): print(*a, flush=True)
def head(t): say(f"\n{'=' * 8} {t} {'=' * (60 - len(t))}")


def _ctx(args):
    d = RUNS / args.seed
    doc = json.loads((d / "pool.json").read_text())
    return originator.Ctx(chain.Chain(args.rpc, ledger=doc["ledger"]), d)


def _verify(pool, rpc, out=None):
    r = verifier.verify(pool, rpc)
    say(verifier.format_result(r))
    if out:
        Path(out).write_text(report.render(r), encoding="utf-8")
        say(f"(trustee report: {out})")
    return r


def demo(args):
    d = RUNS / args.seed
    say("SYNTHETIC DATA. Ariadne demo, seed " + args.seed)
    truth = generate.write(args.seed, d)
    units, ents, meta, truth = generate.load(d)
    anchor = date.fromisoformat(meta["anchor"])
    proc = chain.start_anvil(wire.epoch(anchor), args.port)
    rpc = f"http://127.0.0.1:{args.port}"
    try:
        ch = chain.Chain(rpc)
        say(f"Deployed AriadneLedger at {ch.deploy()} (chain {ch.chain_id})")
        say(f"Replaying {len(units)} factoring units across 3 platforms ({truth['counts']['entities']} entities)...")
        originator.setup(ch, units)
        head("BUILD THE POOL")
        originator.build_pool(ch, d)
        pool = d / "pool.json"
        ctx = originator.Ctx(ch, d)

        head("ACT 1: ONE INVOICE'S JOURNEY")
        say(verifier.trace(pool, rpc, truth["hero"]))

        head("ACT 2: THE ATTACKS")
        results = [originator.attack_stale_owner(ctx), originator.attack_duplicate(ctx),
                   originator.attack_double_pool(ctx)]
        say(f"\nAttacks rejected with no state change: {sum(results)}/3")

        head("ACT 4: THE TRUSTEE'S CHECK")
        r1 = _verify(pool, rpc, d / "report.html")
        originator.lifecycle(ctx)
        r2 = _verify(pool, rpc, d / "report_after_lifecycle.html")

        head("ACT 4b: A SWAP THAT BREAKS THE CAP")
        originator.bad_swap(ctx)
        say("Forced through with a compromised engine key:")
        originator.bad_swap(ctx, force=True)
        r3 = _verify(pool, rpc, d / "report_after_forced.html")

        head("SUMMARY")
        say(f"attacks rejected with no state change: {sum(results)}/3")
        say(f"verifier: sealed pool {r1['status']}, after lifecycle {r2['status']}, after forced bad swap {r3['status']}")
        ok = all(results) and r1["status"] == r2["status"] == "VERIFIED" and r3["status"] == "NOT VERIFIED"
        say("DEMO RESULT: " + ("as designed" if ok else "UNEXPECTED, investigate"))
        if args.keep:
            say(f"\nChain left running at {rpc}. Ctrl+C to stop.")
            proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        proc.kill()


def main():
    ap = argparse.ArgumentParser(prog="ariadne")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("demo"); s.add_argument("--seed", default="A"); s.add_argument("--port", type=int, default=8545)
    s.add_argument("--keep", action="store_true"); s.set_defaults(fn=demo)
    s = sub.add_parser("generate"); s.add_argument("--seed", default="A")
    s.set_defaults(fn=lambda a: say(json.dumps(generate.write(a.seed, RUNS / a.seed)["counts"])))
    s = sub.add_parser("verify"); s.add_argument("pool"); s.add_argument("--rpc", default="http://127.0.0.1:8545")
    s.add_argument("--report"); s.set_defaults(fn=lambda a: _verify(a.pool, a.rpc, a.report))
    s = sub.add_parser("trace"); s.add_argument("pool"); s.add_argument("unit")
    s.add_argument("--rpc", default="http://127.0.0.1:8545")
    s.set_defaults(fn=lambda a: say(verifier.trace(a.pool, a.rpc, a.unit)))
    s = sub.add_parser("attack")
    s.add_argument("kind", choices=["stale-owner", "duplicate", "double-pool", "bad-swap"])
    s.add_argument("--seed", default="A"); s.add_argument("--rpc", default="http://127.0.0.1:8545")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=lambda a: {"stale-owner": originator.attack_stale_owner, "duplicate": originator.attack_duplicate,
                                 "double-pool": originator.attack_double_pool,
                                 "bad-swap": lambda c: originator.bad_swap(c, a.force)}[a.kind](_ctx(a)))
    s = sub.add_parser("metrics"); s.set_defaults(fn=lambda a: say(__import__("ariadne.metrics", fromlist=["run"]).run()))
    s = sub.add_parser("lifecycle"); s.add_argument("--seed", default="A")
    s.add_argument("--rpc", default="http://127.0.0.1:8545"); s.set_defaults(fn=lambda a: originator.lifecycle(_ctx(a)))
    args = ap.parse_args()
    args.fn(args)
