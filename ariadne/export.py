"""Record a full live run into site/, a static read-only copy of the UI for hosting.

The recording comes from a real run on a local chain: attacks fired, lifecycle steps taken, the
forced bad swap and the verifier's answer at three points in time. The hosted page cannot fire
anything; it replays what was recorded, says so on every page, and recomputes the pool's
membership commitment in the visitor's browser from the recorded events.
"""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import report, server, verifier, wire
from .chain import ROOT


def _jsonable(o):
    return wire.hx(o) if isinstance(o, (bytes, bytearray)) else str(o)


def export(out: Path = ROOT / "site", seed: str = "A", port: int = 8621):
    d = server.Demo(port)
    try:
        d.boot_sync(seed, auto=True)
        if d.phase != "ready":
            raise SystemExit(f"could not build the demo: {d.error}")
        states = []

        def capture(sid, label):
            v = d.verify()
            states.append({"id": sid, "label": label, "block": d.ch.w3.eth.block_number, "pool": d.pool(),
                           "verify": v, "units": d.units_list()})

        capture("sealed", "Sealed")
        attacks = {k: d.attack(k) for k in ("stale-owner", "duplicate", "double-pool")}
        actions = {k: d.action(k) for k in ("settle", "substitute", "default")}
        capture("lifecycle", "Lifecycle")
        actions["swap-refused"] = d.action("swap-refused")
        actions["swap-forced"] = d.action("swap-forced")
        capture("forced", "Forced swap")
        for a in actions.values():  # the state selector, not a click, decides what is verified
            a.pop("verify", None)

        d.compiler_parse(None)
        d.compiler_decide({r["id"]: {"status": "APPROVED"} for r in d.compiler["rules"]})
        signed = d.compiler_sign()
        compiler_rec = {"parse": {**d.compiler, "sample": True}, "sign": signed}

        # every unit's events, and the membership events for the in-browser commitment replay
        labels = {k: f"{v['platform']}:{v['unit_no']}" for k, v in d.ctx.rows.items()}
        ts_cache, traces, timeline = {}, {}, []
        ts = lambda b: ts_cache.setdefault(b, d.ch.w3.eth.get_block(b).timestamp)
        for name, a, block in verifier._events(d.ch.w3, d.ch.contract):
            if name == "MembershipChanged" and a["poolId"] == d.ctx.pid:
                timeline.append({"op": a["op"], "unitId": wire.hx(a["unitId"]), "commitment": wire.hx(a["commitment"]), "block": block})
            uid = a.get("unitId")
            if uid is None or uid not in labels:
                continue
            e = {"event": name, "block": block, "ts": ts(block)}
            for k, v in a.items():
                if k in ("unitId", "buyerKey", "sellerKey", "receivableKey"):
                    continue
                e[k] = wire.hx(v) if isinstance(v, (bytes, bytearray)) else v
            if name == "MembershipChanged":
                e["op"] = verifier.OPNAMES[a["op"]]
            for k in ("registrar", "financier", "from", "to", "originator"):
                if k in e:
                    e[k] = d.ch.names.get(e[k], e[k])
            if "poolId" in e:
                e["pool"] = d.pools.get(e["poolId"], e["poolId"][:10])
            traces.setdefault(labels[uid], []).append(e)

        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        except OSError:
            commit = "unknown"
        snap = {
            "meta": {"seed": seed, "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "commit": commit or "unknown",
                     "ledger": d.ch.contract.address, "hero": d.truth["hero"], "synthetic": True},
            "status": d.status(), "states": states, "attacks": attacks, "actions": actions,
            "evidence": d.evidence(), "metrics": d.metrics(), "traces": traces, "timeline": timeline,
            "compiler": compiler_rec,
        }
        out.mkdir(parents=True, exist_ok=True)
        for f in (ROOT / "ui").iterdir():
            if f.suffix in (".js", ".css"):
                shutil.copy(f, out / f.name)
        html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        for a, b in (('href="/style.css"', 'href="style.css"'), ('src="/app.js"', 'src="app.js"'), ('src="/content.js"', 'src="content.js"'),
                     ('src="/pages2.js"', 'src="pages2.js"'), ('src="/story.js"', 'src="story.js"')):
            html = html.replace(a, b)
        (out / "index.html").write_text(html, encoding="utf-8")
        (out / "report.html").write_text(report.render(states[0]["verify"]), encoding="utf-8")
        (out / "snapshot.json").write_text(json.dumps(snap, default=_jsonable), encoding="utf-8")
        (out / ".nojekyll").write_text("")
        print(f"Wrote {out} ({len(snap['traces'])} unit traces, {len(timeline)} membership events, commit {snap['meta']['commit']})")
    finally:
        d.stop_chain()
