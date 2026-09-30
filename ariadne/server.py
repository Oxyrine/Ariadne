"""Web UI server. Stdlib only: a small JSON API over the same code the CLI uses, plus static files.

Every action goes to the contract for real; the UI never fakes a result. One demo session at a time.
"""
import json
import mimetypes
import tempfile
import threading
import traceback
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import chain, compiler, engine, evidence, generate, metrics, originator, report, verifier, wire
from .chain import ROOT

UI = ROOT / "ui"
RUNS = ROOT / "runs"
STEPS = ["Generate synthetic data", "Start local chain", "Deploy the ledger contract",
         "Replay units onto the chain", "Build the pool"]
BUILD_STAGES = {"rules": "Pin the signed rules", "pool": "Create the pool on-chain", "select": "Confirm ownership and unit rules",
                "trim": "Trim for concentration", "attest": "Attest and add units", "seal": "Seal the pool"}


class Demo:
    def __init__(self, anvil_port=8545):
        self.lock = threading.RLock()
        self.anvil_port = anvil_port
        self.proc = None
        self.reset_state()

    def reset_state(self):
        self.phase, self.error, self.seed = "idle", None, None
        self.step_i, self.detail, self.log = -1, "", []
        self.ch = self.ctx = None
        self.units = self.ents = self.meta = self.truth = self.diag = self.dir = None
        self.gname, self.pk, self.pools = {}, {}, {}
        self.last_verify, self._snap = None, None
        self.pool_built = False
        self.build = {"running": False, "stage": None, "i": 0, "n": 0, "error": None}
        self.draft = {"review": {}, "groups": {}, "rules": None, "rules_source": "default"}
        self.compiler = None
        self.attacks, self.flags = [], {}

    # ------------------------------------------------------------- lifecycle

    def stop_chain(self):
        if self.proc:
            self.proc.kill()
            self.proc = None

    def reset(self):
        with self.lock:
            self.stop_chain()
            self.reset_state()

    def start(self, seed, auto=False):
        if self.phase == "starting":
            return
        self.reset()
        self.phase, self.seed = "starting", seed
        threading.Thread(target=self._boot, args=(seed, auto), daemon=True).start()

    def _step(self, i, detail=""):
        self.step_i, self.detail = i, detail

    def boot_sync(self, seed, auto=True):
        self.reset()
        self.phase, self.seed = "starting", seed
        self._boot(seed, auto)

    def _boot(self, seed, auto):
        old = originator.SINK
        originator.SINK = self.log.append
        try:
            d = self.dir = RUNS / seed
            self._step(0)
            generate.write(seed, d)
            self.units, self.ents, self.meta, self.truth = generate.load(d)
            anchor = date.fromisoformat(self.meta["anchor"])
            self._step(1)
            self.proc = chain.start_anvil(wire.epoch(anchor), self.anvil_port)
            self.ch = chain.Chain(f"http://127.0.0.1:{self.anvil_port}")
            self._step(2)
            self.ch.deploy()
            self._step(3, f"0 of {len(self.units)}")
            originator.setup(self.ch, self.units,
                             progress=lambda i, n: self._step(3, f"{i} of {n}") if i % 10 == 0 else None)
            self._index_entities()
            if auto:
                self._step(4)
                self._build(auto=True)
            self._step(5)
            self.phase = "ready"
        except Exception as e:
            traceback.print_exc()
            self.phase, self.error = "error", f"{type(e).__name__}: {e}"
            self.stop_chain()
        finally:
            originator.SINK = old

    def _index_entities(self):
        self.gname = {g: e["name"] for g, e in self.ents.items()}
        self.pk = {wire.hx(wire.party_key(g)): g for g in self.ents}

    def _progress(self, stage, i, n):
        self.build.update(stage=stage, i=i, n=n)
        self._step(4, f"{BUILD_STAGES.get(stage, stage)}" + (f" ({i} of {n})" if n > 1 else ""))

    def _build(self, auto=False):
        """Run the pool build with the reviewer's decisions (or the demo defaults) and open the pool."""
        self.build.update(running=True, error=None)
        try:
            originator.build_pool(
                self.ch, self.dir, rs=self.draft["rules"],
                review=None if auto else self.draft["review"],
                group_decisions=None if auto else self.draft["groups"], progress=self._progress)
            self.ctx = originator.Ctx(self.ch, self.dir)
            self.diag = evidence.score_units(self.units, self.ents, date.fromisoformat(self.meta["anchor"]))[1]
            self.pools = {wire.hx(self.ctx.pid): originator.POOL_NAME, wire.hx(wire.pool_id("demo-pool-02")): "demo-pool-02"}
            self.pool_built, self._snap = True, None
            self.flags["s1"] = all(self.ctx.ev[wire.hx(originator.uid_of(r))]["reviewStatus"] in ("EXCLUDED", "PENDING_REVIEW")
                                   for r in self.units if originator.label(r) in self.truth["fabricated_loop"])
            groups = [set(g["members"]) for g in self.ctx.gm["evidence"].values()]
            self.flags["s3"] = any(set(self.truth["groups"][-2 if len(self.truth["groups"]) > 1 else 0]) <= g for g in groups) \
                if self.truth["groups"] else False
        except Exception as e:
            self.build["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            self.build["running"] = False

    def build_async(self, auto):
        with self.lock:
            self.require_ready()
            if self.pool_built:
                raise RuntimeError("the pool is already built; reset to start over")
            if self.build["running"]:
                return

        def run():
            old = originator.SINK
            originator.SINK = self.log.append
            try:
                self._build(auto)
            except Exception:
                traceback.print_exc()
            finally:
                originator.SINK = old
        self.build["running"] = True
        threading.Thread(target=run, daemon=True).start()

    @contextmanager
    def capture(self):
        lines, old = [], originator.SINK
        originator.SINK = lines.append
        originator.LAST.clear()
        originator.RESULT.clear()
        try:
            yield lines
        finally:
            originator.SINK = old

    def require_ready(self):
        if self.phase != "ready":
            raise RuntimeError("the demo is not running: start it first")

    def require_pool(self):
        self.require_ready()
        if not self.pool_built:
            raise RuntimeError("the pool is not built yet: build it first")

    # ---------------------------------------------------------------- reads

    def status(self):
        s = {"phase": self.phase, "error": self.error, "seed": self.seed, "steps": STEPS, "step": self.step_i,
             "detail": self.detail, "log": self.log[-60:], "poolBuilt": self.pool_built, "build": dict(self.build),
             "buildStages": BUILD_STAGES}
        if self.phase == "ready":
            with self.lock:
                s.update(block=self.ch.w3.eth.block_number, chainTime=self.ch.ts(), ledger=self.ch.contract.address,
                         anchor=self.meta["anchor"], units=len(self.units), heroLabel=self.truth["hero"])
        return s

    def snapshot(self):
        """Chain state of every unit, cached per block."""
        block = self.ch.w3.eth.block_number
        if self._snap and self._snap[0] == block:
            return self._snap[1]
        ev, gm = self.ctx.ev, self.ctx.gm["groups"]
        rows = []
        for uid, r in self.ctx.rows.items():
            u = self.ch.unit(uid)
            e = ev.get(wire.hx(uid), {})
            bk = wire.hx(u["buyerKey"])
            rows.append({
                "label": originator.label(r), "seller": self.gname[r["seller_gstin"]], "buyer": self.gname[r["buyer_gstin"]],
                "buyerKey": bk, "group": gm.get(bk), "amountPaise": u["amountPaise"], "rating": e.get("buyerRating"),
                "invoiceDate": r["invoice_date"].isoformat(), "dueDate": r["due_date"].isoformat(),
                "owner": self.ch.names.get(u["owner"]), "state": wire.STATES[u["state"]],
                "pool": self.pools.get(wire.hx(u["currentPoolId"])), "transfers": u["transferCount"],
                "score": e.get("evidenceScore", 0), "review": e.get("reviewStatus", "NONE"),
                "reserve": bool(r["reserve"]), "platform": r["platform"]})
        self._snap = (block, rows)
        return rows

    def _group_shares(self, members, gm, top=8):
        total = sum(m["amountPaise"] for m in members) or 1
        by = {}
        for m in members:
            gid = gm["groups"].get(m["buyerKey"])
            name = gid or self.gname[self.pk[m["buyerKey"]]]
            g = by.setdefault(name, {"name": name, "amountPaise": 0, "merged": bool(gid)})
            g["amountPaise"] += m["amountPaise"]
        out = sorted(by.values(), key=lambda g: -g["amountPaise"])[:top]
        for g in out:
            g["bps"] = g["amountPaise"] * 10_000 // total
        return out

    def pool(self):
        with self.lock:
            self.require_pool()
            c, pid = self.ch.contract, self.ctx.pid
            o, rh, gh, eh, commit, members, version, sealed = c.functions.pools(pid).call()
            live = [r for r in self.snapshot() if r["pool"] == originator.POOL_NAME and r["state"] == "POOLED"]
            total = sum(r["amountPaise"] for r in live) or 1
            by, sellers = {}, {}
            for r in live:
                key = r["group"] or r["buyer"]
                g = by.setdefault(key, {"name": key, "amountPaise": 0, "units": 0, "merged": bool(r["group"])})
                g["amountPaise"] += r["amountPaise"]
                g["units"] += 1
                sellers[r["seller"]] = sellers.get(r["seller"], 0) + r["amountPaise"]
            groups = sorted(by.values(), key=lambda g: -g["amountPaise"])[:10]
            merged = {gid: e["members"] for gid, e in self.ctx.gm["evidence"].items()}
            for g in groups:
                g["bps"] = g["amountPaise"] * 10_000 // total
                g["memberNames"] = [self.gname[x] for x in merged.get(g["name"], [])]
            top_sellers = sorted(({"name": k, "bps": v * 10_000 // total} for k, v in sellers.items()), key=lambda s: -s["bps"])[:6]
            rules = self.ctx.rs["rules"]
            cap = lambda kind: next((r["value"] for r in rules if r["kind"] == kind), None)
            return {"name": originator.POOL_NAME, "sealed": sealed, "manifest": version, "members": members,
                    "commitment": wire.hx(commit), "rulesHash": wire.hx(rh), "originator": self.ch.names.get(o),
                    "totalPaise": total, "groups": groups, "sellers": top_sellers,
                    "capBps": cap("buyer_group_concentration_max_bps"), "sellerCapBps": cap("seller_concentration_max_bps"),
                    "rules": rules, "signedBy": self.ctx.rs["signedBy"], "rulesSource": self.draft["rules_source"]}

    def units_list(self):
        with self.lock:
            self.require_pool()
            return self.snapshot()

    def trace(self, label):
        with self.lock:
            self.require_pool()
            uid = self.ctx.by_label(label)
            events = verifier.trace_events(self.ch.contract, self.ch.w3, uid)
            for e in events:
                for k in ("registrar", "financier", "from", "to", "originator"):
                    if k in e:
                        e[k] = self.ch.names.get(e[k], e[k])
                if "poolId" in e:
                    e["pool"] = self.pools.get(e["poolId"], e["poolId"][:10])
            row = next(r for r in self.snapshot() if r["label"] == label)
            return {"unit": row, "events": events, "unitId": wire.hx(uid)}

    def evidence(self):
        with self.lock:
            self.require_pool()
            units = self.ctx.ev
            cycles = []
            for cid, c in self.diag["cycles"].items():
                mine = [e for e in units.values() if e["cycleId"] == cid]
                feats = mine[0]["features"] if mine else {}
                by_ent = {}
                for r in self.units:
                    if r["seller_gstin"] in c["nodes"] and r["buyer_gstin"] in c["nodes"]:
                        by_ent[(r["seller_gstin"], r["buyer_gstin"])] = by_ent.get((r["seller_gstin"], r["buyer_gstin"]), 0) + r["amount_paise"]
                legs = [{"from": self.gname[c["nodes"][i]], "to": self.gname[c["nodes"][(i + 1) % len(c["nodes"])]],
                         "amountPaise": by_ent.get((c["nodes"][i], c["nodes"][(i + 1) % len(c["nodes"])]), 0)}
                        for i in range(len(c["nodes"]))]
                cycles.append({"id": cid, "score": c["score"], "features": feats, "legs": legs,
                               "entities": [self.gname[g] for g in c["nodes"]],
                               "units": [{"label": e["label"], "review": e["reviewStatus"]} for e in mine]})
            cycles.sort(key=lambda c: -c["score"])
            groups = [{"id": gid, "members": [self.gname[g] for g in e["members"]], "links": e["links"]}
                      for gid, e in self.ctx.gm["evidence"].items()]
            cands = [{**c, "memberNames": [self.gname[g] for g in c["members"]]} for c in self.ctx.gm["candidates"]]
            return {"cycles": cycles, "groups": groups, "candidates": cands, "cutoff": evidence.CUTOFF,
                    "weights": evidence.WEIGHTS}

    # ------------------------------------------------------- wizard (build)

    def current_rules(self):
        if not self.draft["rules"]:
            self.draft["rules"] = originator.sign_rules(self.ch)
            self.draft["rules_source"] = "default"
        return self.draft["rules"]

    def propose(self):
        with self.lock:
            self.require_ready()
            rs = self.current_rules()
            prep, rs, rows = originator.propose(self.ch, self.dir, self.draft["review"], self.draft["groups"], rs)
            ev, gm = prep["ev"], prep["gm"]
            units = []
            for x in rows:
                r, e = x["row"], ev[wire.hx(x["uid"])]
                units.append({"label": originator.label(r), "uid": wire.hx(x["uid"]), "seller": self.gname[r["seller_gstin"]],
                              "buyer": self.gname[r["buyer_gstin"]], "rating": e.get("buyerRating"), "score": e["evidenceScore"],
                              "review": e["reviewStatus"], "group": e.get("buyerGroupId"), "spare": bool(r["reserve"]),
                              "owner": self.ch.names.get(x["view"]["owner"]), "ownerOk": x["view"]["owner"] == self.ch.acct["B"],
                              "amountPaise": r["amount_paise"], "ok": x["ok"],
                              "fails": [{"rule": f["rule"], "kind": f["kind"], "detail": f["detail"]} for f in x["fails"]]})
            cands = [x["view"] for x in rows if x["ok"]]
            members, dropped = originator.trim(cands, gm, rs)
            loops = []
            for cid, c in prep["diag"]["cycles"].items():
                mine = [e for e in ev.values() if e["cycleId"] == cid]
                loops.append({"id": cid, "score": c["score"], "held": c["score"] > evidence.CUTOFF,
                              "entities": [self.gname[g] for g in c["nodes"]], "features": mine[0]["features"] if mine else {},
                              "units": [{"uid": e["unitId"], "label": e["label"], "review": e["reviewStatus"]} for e in mine]})
            loops.sort(key=lambda l: -l["score"])
            return {
                "units": units, "loops": loops, "rules": rs, "rulesSource": self.draft["rules_source"],
                "candidates": [{**c, "memberNames": [self.gname[g] for g in c["members"]]} for c in gm["candidates"]],
                "groups": [{"id": gid, "members": [self.gname[g] for g in e["members"]], "links": e["links"]}
                           for gid, e in gm["evidence"].items()],
                "counts": {"total": len(units), "eligible": len(cands), "held": len(prep["held"]),
                           "afterTrim": len(members), "trimmed": len(dropped),
                           "pendingLoops": sum(1 for u in units if u["review"] == "PENDING_REVIEW"),
                           "pendingCandidates": sum(1 for c in gm["candidates"] if c["status"] == "PENDING")},
                "shares": {"before": self._group_shares(cands, gm), "after": self._group_shares(members, gm)},
                "capBps": next((r["value"] for r in rs["rules"] if r["kind"] == "buyer_group_concentration_max_bps"), None),
                "sealedSize": len(members), "minSize": next((r["value"] for r in rs["rules"] if r["kind"] == "min_pool_size"), 0),
                "decisions": {"review": self.draft["review"], "groups": self.draft["groups"]}}

    def set_review(self, decisions):
        with self.lock:
            self.require_ready()
            for uid, d in decisions.items():
                if d not in ("EXCLUDED", "CLEARED", None):
                    raise ValueError("decision must be EXCLUDED or CLEARED")
                self.draft["review"].pop(uid, None) if d is None else self.draft["review"].__setitem__(uid, d)
        return self.propose()

    def set_groups(self, decisions):
        with self.lock:
            self.require_ready()
            for cid, d in decisions.items():
                if d not in ("CONFIRMED", "REJECTED", None):
                    raise ValueError("decision must be CONFIRMED or REJECTED")
                self.draft["groups"].pop(cid, None) if d is None else self.draft["groups"].__setitem__(cid, d)
        return self.propose()

    def use_default_rules(self):
        with self.lock:
            self.require_ready()
            self.draft["rules"] = originator.sign_rules(self.ch)
            self.draft["rules_source"] = "default"
            return {"rules": self.draft["rules"], "hash": wire.hx(wire.rules_hash(self.draft["rules"]))}

    # -------------------------------------------------------------- compiler

    def compiler_parse(self, text=None):
        with self.lock:
            text = (text or "").strip() or compiler.sample_text()
            out = compiler.parse(text)
            self.compiler = {"text": text, **out}
            return {**self.compiler, "sample": text == compiler.sample_text().strip() or text == compiler.sample_text()}

    def compiler_decide(self, decisions):
        with self.lock:
            if not self.compiler:
                raise RuntimeError("parse a document first")
            for r in self.compiler["rules"]:
                d = decisions.get(r["id"])
                if d:
                    r["status"] = d["status"]
                    if "value" in d:
                        r["value"] = d["value"]
                        r["readBack"] = compiler.read_back(r["kind"], r["value"])
                        r["status"] = "EDITED"
            return self.compiler

    def compiler_sign(self):
        with self.lock:
            self.require_ready()
            if not self.compiler:
                raise RuntimeError("parse a document first")
            rs = compiler.to_ruleset(self.compiler["rules"], self.compiler["unsupported"], self.compiler["text"], originator.POOL_NAME)
            if not rs["rules"]:
                raise ValueError("approve at least one rule before signing")
            signed = engine.sign_ruleset(rs, self.ch.key["rules"])
            engine.validate_ruleset(signed)
            self.draft["rules"], self.draft["rules_source"] = signed, "compiled"
            return {"rules": signed, "hash": wire.hx(wire.rules_hash(signed)), "signer": signed["signedBy"]}

    # -------------------------------------------------------------- actions

    def verify(self):
        with self.lock:
            self.require_pool()
            self.last_verify = verifier.verify(self.ctx.dir / "pool.json", self.ch.rpc)
            return self.last_verify

    def attack(self, kind):
        with self.lock:
            self.require_pool()
            fn = {"stale-owner": originator.attack_stale_owner, "duplicate": originator.attack_duplicate,
                  "double-pool": originator.attack_double_pool}[kind]
            with self.capture() as lines:
                ok = fn(self.ctx)
            self._snap = None
            res = dict(originator.LAST)
            self.attacks.append({"kind": kind, "rejected": bool(res.get("error")), "unchanged": bool(res.get("unchanged"))})
            return {"ok": ok, "result": res, "log": lines}

    def action(self, kind):
        with self.lock:
            self.require_pool()
            fn = {"settle": originator.settle_some, "substitute": originator.substitute_good,
                  "default": originator.default_one, "swap-refused": lambda c: originator.bad_swap(c, False),
                  "swap-forced": lambda c: originator.bad_swap(c, True)}[kind]
            with self.capture() as lines:
                ok = fn(self.ctx)
            self._snap = None
            out = {"ok": bool(ok) or kind == "default", "result": {**originator.RESULT, **originator.LAST}, "log": lines}
            if kind in ("settle", "substitute", "default", "swap-forced"):
                out["verify"] = self.verify()
            if kind == "swap-refused":
                self.flags["refused"] = bool(originator.LAST.get("fails"))
            if kind == "swap-forced":
                self.flags["forced_caught"] = out["verify"]["status"] != "VERIFIED"
            return out

    def report_html(self):
        with self.lock:
            self.require_pool()
            return report.render(self.last_verify or self.verify())

    # --------------------------------------------------------------- metrics

    def metrics(self):
        with self.lock:
            v = self.last_verify
            fails = v["failures"] if v else []
            planted = [
                ("Fabricated trading loop held for review", self.flags.get("s1")),
                ("Buyer group hidden behind four GSTINs resolved", self.flags.get("s3")),
                ("Stale-owner pooling attempt refused", self._attack_ok("stale-owner")),
                ("Duplicate registration refused", self._attack_ok("duplicate")),
                ("Double-pool attempt refused", self._attack_ok("double-pool")),
                ("Cap-breaking swap refused by the engine", self.flags.get("refused")),
                ("Forced bad attestation caught by the verifier", self.flags.get("forced_caught")),
            ]
            run = [p for p in planted if p[1] is not None]
            caught = [p for p in run if p[1]]
            unauth = sum(1 for a in self.attacks if not a["rejected"])
            if "det" not in _CACHE:  # deterministic and slow-ish: compute once per server
                with tempfile.TemporaryDirectory() as tmp:
                    _CACHE["det"] = {s: metrics.detect(s, Path(tmp) / s) for s in "ABC"}
                _CACHE["bench"] = metrics.benchmark()
            detection = _CACHE["det"]
            compiler_bad = 0
            if self.compiler:
                compiler_bad = sum(1 for r in self.compiler["rules"] if r["sourceSpan"] not in self.compiler["text"])
            return {
                "headline": {"planted": len(planted), "exercised": len(run), "caught": len(caught), "unauthorised": unauth},
                "scenarios": [{"name": n, "status": ("caught" if s else "missed") if s is not None else "not yet run"} for n, s in planted],
                "guarantees": [
                    {"name": "Unauthorised moves that succeeded", "value": unauth, "note": f"{len(self.attacks)} attacks fired this session"},
                    {"name": "Receivables live in two pools", "value": sum(1 for f in fails if f["check"] == "RECEIVABLE_UNIQUE") if v else None},
                    {"name": "Commitment mismatches", "value": (0 if v and v["checks"]["COMMITMENT_REPLAY"]["status"] == "PASS" else 1) if v else None},
                    {"name": "Ineligible units attested and not flagged", "value": 0 if v else None,
                     "note": f"{len({f['unit'] for f in fails if f['check'] == 'ATTESTATIONS'})} flagged by the verifier" if v else ""},
                    {"name": "Compiler rules without source words", "value": compiler_bad if self.compiler else None},
                ],
                "detection": detection, "benchmark": _CACHE["bench"],
            }

    def _attack_ok(self, kind):
        xs = [a for a in self.attacks if a["kind"] == kind]
        return None if not xs else all(a["rejected"] and a["unchanged"] for a in xs)


_CACHE = {}
DEMO = Demo()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or "json" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _api(self, method, path, q, body):
        d = DEMO
        routes = {
            ("GET", "/api/status"): d.status, ("GET", "/api/pool"): d.pool, ("GET", "/api/units"): d.units_list,
            ("GET", "/api/evidence"): d.evidence, ("GET", "/api/metrics"): d.metrics,
            ("GET", "/api/trace"): lambda: d.trace(q["unit"][0]),
            ("POST", "/api/start"): lambda: (d.start(body.get("seed", "A"), bool(body.get("auto"))), d.status())[1],
            ("POST", "/api/reset"): lambda: (d.reset(), d.status())[1],
            ("POST", "/api/verify"): d.verify,
            ("POST", "/api/attack"): lambda: d.attack(body["kind"]),
            ("POST", "/api/action"): lambda: d.action(body["kind"]),
            ("POST", "/api/propose"): d.propose,
            ("POST", "/api/review"): lambda: d.set_review(body["decisions"]),
            ("POST", "/api/groups"): lambda: d.set_groups(body["decisions"]),
            ("POST", "/api/rules/default"): d.use_default_rules,
            ("POST", "/api/build"): lambda: (d.build_async(bool(body.get("auto"))), d.status())[1],
            ("POST", "/api/compiler/parse"): lambda: d.compiler_parse(body.get("text")),
            ("POST", "/api/compiler/decide"): lambda: d.compiler_decide(body["decisions"]),
            ("POST", "/api/compiler/sign"): d.compiler_sign,
        }
        return routes[(method, path)]()

    def _handle(self, method):
        u = urlparse(self.path)
        try:
            if u.path == "/api/report":
                return self._send(200, DEMO.report_html(), "text/html")
            if u.path.startswith("/api/"):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                return self._send(200, self._api(method, u.path, parse_qs(u.query), body))
            rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (UI / rel).resolve()
            if UI.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        except KeyError:
            self._send(404, {"error": "unknown route or missing field"})
        except (ValueError, RuntimeError) as e:
            self._send(400 if isinstance(e, ValueError) else 500, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")


def serve(port=8000, anvil_port=8545, open_browser=False):
    DEMO.anvil_port = anvil_port
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Ariadne UI at http://127.0.0.1:{port}  (synthetic data)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        DEMO.stop_chain()
