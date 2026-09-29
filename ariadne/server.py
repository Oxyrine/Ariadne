"""Web UI server. Stdlib only: a small JSON API over the same code the CLI uses, plus static files.

Every action goes to the contract for real; the UI never fakes a result. One demo session at a time.
"""
import json
import mimetypes
import threading
import traceback
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import chain, engine, evidence, generate, originator, report, verifier, wire
from .chain import ROOT

UI = ROOT / "ui"
RUNS = ROOT / "runs"
STEPS = ["Generate synthetic data", "Start local chain", "Deploy the ledger contract",
         "Replay units onto the chain", "Score evidence and build the pool", "Seal the pool"]


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
        self.units = self.ents = self.meta = self.truth = self.diag = None
        self.names, self.last_verify, self._snap = {}, None, None

    # ------------------------------------------------------------- lifecycle

    def stop_chain(self):
        if self.proc:
            self.proc.kill()
            self.proc = None

    def reset(self):
        with self.lock:
            self.stop_chain()
            self.reset_state()

    def start(self, seed):
        if self.phase == "starting":
            return
        self.reset()
        self.phase, self.seed = "starting", seed
        threading.Thread(target=self._boot, args=(seed,), daemon=True).start()

    def _step(self, i, detail=""):
        self.step_i, self.detail = i, detail

    def _boot(self, seed):
        old = originator.SINK
        originator.SINK = self.log.append
        try:
            d = RUNS / seed
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
            self._step(4)
            ev, self.diag = evidence.score_units(self.units, self.ents, anchor)
            originator.build_pool(self.ch, d)
            self._step(5)
            self.ctx = originator.Ctx(self.ch, d)
            self._index_names()
            self._step(6)
            self.phase = "ready"
        except Exception as e:
            traceback.print_exc()
            self.phase, self.error = "error", f"{type(e).__name__}: {e}"
            self.stop_chain()
        finally:
            originator.SINK = old

    def _index_names(self):
        self.gname = {g: e["name"] for g, e in self.ents.items()}
        self.pk = {wire.hx(wire.party_key(g)): g for g in self.ents}
        self.pools = {wire.hx(self.ctx.pid): originator.POOL_NAME,
                      wire.hx(wire.pool_id("demo-pool-02")): "demo-pool-02"}

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

    # ---------------------------------------------------------------- reads

    def status(self):
        s = {"phase": self.phase, "error": self.error, "seed": self.seed, "steps": STEPS,
             "step": self.step_i, "detail": self.detail, "log": self.log[-60:]}
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
        ev = self.ctx.ev
        gm = self.ctx.gm["groups"]
        rows = []
        for uid, r in self.ctx.rows.items():
            u = self.ch.unit(uid)
            e = ev.get(wire.hx(uid), {})
            bk = wire.hx(u["buyerKey"])
            rows.append({
                "label": originator.label(r), "seller": self.gname[r["seller_gstin"]], "buyer": self.gname[r["buyer_gstin"]],
                "buyerKey": bk, "group": gm.get(bk), "amountPaise": u["amountPaise"],
                "invoiceDate": r["invoice_date"].isoformat(), "dueDate": r["due_date"].isoformat(),
                "owner": self.ch.names.get(u["owner"]), "state": wire.STATES[u["state"]],
                "pool": self.pools.get(wire.hx(u["currentPoolId"])), "transfers": u["transferCount"],
                "score": e.get("evidenceScore", 0), "review": e.get("reviewStatus", "NONE"),
                "reserve": bool(r["reserve"]), "platform": r["platform"]})
        self._snap = (block, rows)
        return rows

    def pool(self):
        with self.lock:
            self.require_ready()
            c, pid = self.ch.contract, self.ctx.pid
            o, rh, gh, eh, commit, members, version, sealed = c.functions.pools(pid).call()
            live = [r for r in self.snapshot() if r["pool"] == originator.POOL_NAME and r["state"] == "POOLED"]
            total = sum(r["amountPaise"] for r in live) or 1
            by = {}
            for r in live:
                key = r["group"] or r["buyer"]
                g = by.setdefault(key, {"name": key, "amountPaise": 0, "units": 0, "merged": bool(r["group"])})
                g["amountPaise"] += r["amountPaise"]
                g["units"] += 1
            groups = sorted(by.values(), key=lambda g: -g["amountPaise"])[:10]
            for g in groups:
                g["bps"] = g["amountPaise"] * 10_000 // total
            merged = {gid: e["members"] for gid, e in self.ctx.gm["evidence"].items()}
            for g in groups:
                g["memberNames"] = [self.gname[x] for x in merged.get(g["name"], [])]
            cap = next(r["value"] for r in self.ctx.rs["rules"] if r["kind"] == "buyer_group_concentration_max_bps")
            return {"name": originator.POOL_NAME, "sealed": sealed, "manifest": version, "members": members,
                    "commitment": wire.hx(commit), "rulesHash": wire.hx(rh), "originator": self.ch.names.get(o),
                    "totalPaise": total, "groups": groups, "capBps": cap,
                    "rules": self.ctx.rs["rules"], "signedBy": self.ctx.rs["signedBy"]}

    def units_list(self):
        with self.lock:
            self.require_ready()
            return self.snapshot()

    def trace(self, label):
        with self.lock:
            self.require_ready()
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
            self.require_ready()
            units = self.ctx.ev
            cycles = []
            for cid, c in self.diag["cycles"].items():
                mine = [e for e in units.values() if e["cycleId"] == cid]
                feats = mine[0]["features"] if mine else {}
                cycles.append({"id": cid, "score": c["score"], "features": feats,
                               "entities": [self.gname[g] for g in c["nodes"]],
                               "units": [{"label": e["label"], "review": e["reviewStatus"]} for e in mine]})
            cycles.sort(key=lambda c: -c["score"])
            groups = [{"id": gid, "members": [self.gname[g] for g in e["members"]], "links": e["links"]}
                      for gid, e in self.ctx.gm["evidence"].items()]
            return {"cycles": cycles, "groups": groups, "cutoff": evidence.CUTOFF, "weights": evidence.WEIGHTS}

    # -------------------------------------------------------------- actions

    def verify(self):
        with self.lock:
            self.require_ready()
            self.last_verify = verifier.verify(self.ctx.dir / "pool.json", self.ch.rpc)
            return self.last_verify

    def attack(self, kind):
        with self.lock:
            self.require_ready()
            fn = {"stale-owner": originator.attack_stale_owner, "duplicate": originator.attack_duplicate,
                  "double-pool": originator.attack_double_pool}[kind]
            with self.capture() as lines:
                ok = fn(self.ctx)
            self._snap = None
            return {"ok": ok, "result": dict(originator.LAST), "log": lines}

    def action(self, kind):
        with self.lock:
            self.require_ready()
            fn = {"settle": originator.settle_some, "substitute": originator.substitute_good,
                  "default": originator.default_one, "swap-refused": lambda c: originator.bad_swap(c, False),
                  "swap-forced": lambda c: originator.bad_swap(c, True)}[kind]
            with self.capture() as lines:
                ok = fn(self.ctx)
            self._snap = None
            out = {"ok": bool(ok) or kind == "default", "result": {**originator.RESULT, **originator.LAST}, "log": lines}
            if kind in ("settle", "substitute", "default", "swap-forced"):
                out["verify"] = self.verify()
            return out

    def report_html(self):
        with self.lock:
            self.require_ready()
            return report.render(self.last_verify or self.verify())


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
        self.end_headers()
        self.wfile.write(data)

    def _api(self, method, path, q, body):
        d = DEMO
        routes = {
            ("GET", "/api/status"): d.status,
            ("GET", "/api/pool"): d.pool,
            ("GET", "/api/units"): d.units_list,
            ("GET", "/api/evidence"): d.evidence,
            ("GET", "/api/trace"): lambda: d.trace(q["unit"][0]),
            ("POST", "/api/start"): lambda: (d.start(body.get("seed", "A")), d.status())[1],
            ("POST", "/api/reset"): lambda: (d.reset(), d.status())[1],
            ("POST", "/api/verify"): d.verify,
            ("POST", "/api/attack"): lambda: d.attack(body["kind"]),
            ("POST", "/api/action"): lambda: d.action(body["kind"]),
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
