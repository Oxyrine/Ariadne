"""Layer 3: suspicious-pattern evidence scoring and buyer-group resolution.

Produces evidence for a human, never a verdict. Loop-scoring approach follows the
pattern from Circe (SCC, bounded cycle search, discrimination features); entity
resolution follows Lumine (strong-signal union-find). Both are reimplemented here.

Weights are hand-set constants, not trained: we have no labelled fraud data.
"""
from datetime import date

from . import wire

WEIGHTS = {"value_symmetry": 250, "timing": 200, "entity_age": 150,
           "tightness": 150, "shared_attributes": 150, "round_amounts": 100}
assert sum(WEIGHTS.values()) == 1000
MAX_CYCLE_LEN = 6
MAX_CYCLES_PER_SCC = 2000
CUTOFF = 700
ROUND_PAISE = 100_000  # amounts that are whole multiples of Rs 1,000


def pan_of(gstin: str) -> str:
    return gstin[2:12]


# ------------------------------------------------------------------ graph

def build_graph(units):
    """Edges aggregated per (seller, buyer): list of invoice rows."""
    edges = {}
    for u in units:
        edges.setdefault((u["seller_gstin"], u["buyer_gstin"]), []).append(u)
    adj = {}
    for s, b in edges:
        adj.setdefault(s, set()).add(b)
        adj.setdefault(b, set())
    return edges, {k: sorted(v) for k, v in adj.items()}


def tarjan_scc(adj):
    """Iterative Tarjan, linear in nodes + edges."""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in sorted(adj):
        if root in index:
            continue
        work = [(root, iter(adj[root]))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); on.add(root)
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]; counter[0] += 1
                    stack.append(w); on.add(w)
                    work.append((w, iter(adj[w])))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop(); on.discard(w); comp.append(w)
                        if w == v:
                            break
                    out.append(sorted(comp))
    return out


def find_cycles(adj, comp):
    """Depth-limited enumeration inside one SCC. Each cycle is reported once, rooted at
    its smallest node. ponytail: capped at MAX_CYCLES_PER_SCC; dense components are
    truncated, and the benchmark in metrics.py reports how runtime grows with density."""
    members = set(comp)
    cycles, truncated = [], False
    for s in comp:
        path, seen = [s], {s}

        def dfs(v):
            nonlocal truncated
            if truncated:
                return
            for w in adj[v]:
                if w not in members or w < s:
                    continue
                if w == s and len(path) >= 2:
                    cycles.append(list(path))
                    if len(cycles) >= MAX_CYCLES_PER_SCC:
                        truncated = True
                        return
                elif w not in seen and len(path) < MAX_CYCLE_LEN:
                    path.append(w); seen.add(w)
                    dfs(w)
                    path.pop(); seen.discard(w)
        dfs(s)
        if truncated:
            break
    return cycles, truncated


# --------------------------------------------------------------- features

def score_cycle(cycle, edges, entities, volume, anchor: date):
    n = len(cycle)
    legs = [(cycle[i], cycle[(i + 1) % n]) for i in range(n)]
    leg_units = [edges[l] for l in legs]
    leg_amt = [sum(u["amount_paise"] for u in us) for us in leg_units]
    invoices = [u for us in leg_units for u in us]

    sym_bps = min(leg_amt) * 10_000 // max(leg_amt)
    f = {"value_symmetry": WEIGHTS["value_symmetry"] * sym_bps * sym_bps // 100_000_000}

    firsts = [min(wire.to_date(u["invoice_date"]) for u in us) for us in leg_units]
    span = (max(firsts) - min(firsts)).days
    f["timing"] = WEIGHTS["timing"] * (1000 if span <= 7 else 600 if span <= 30 else 200 if span <= 90 else 0) // 1000

    young = sum(1 for g in cycle if (anchor - wire.to_date(entities[g]["registered"])).days < 365)
    f["entity_age"] = WEIGHTS["entity_age"] * young // n

    inside = sum(leg_amt)
    total = sum(volume[g] for g in cycle) // 2 or 1  # each invoice touches two nodes
    f["tightness"] = WEIGHTS["tightness"] * min(inside, total) // total

    shared = 0
    for g in cycle:
        e = entities[g]
        if any(o != g and (set(e["directors"]) & set(entities[o]["directors"])
                           or e["address"] == entities[o]["address"] or e["bank"] == entities[o]["bank"])
               for o in cycle):
            shared += 1
    f["shared_attributes"] = WEIGHTS["shared_attributes"] * shared // n

    rnd = sum(1 for u in invoices if u["amount_paise"] % ROUND_PAISE == 0)
    f["round_amounts"] = WEIGHTS["round_amounts"] * rnd // len(invoices)
    return sum(f.values()), f


def score_units(units, entities, anchor: date, cutoff: int = CUTOFF):
    """Evidence for every unit. Returns {unitIdHex: {...}} plus loop diagnostics."""
    edges, adj = build_graph(units)
    volume = {}
    for u in units:
        for g in (u["seller_gstin"], u["buyer_gstin"]):
            volume[g] = volume.get(g, 0) + u["amount_paise"]

    best = {}  # unit row id -> (score, features, cycle_id)
    cycle_table, cid, truncated_any = {}, 0, False
    for comp in tarjan_scc(adj):
        if len(comp) < 2:
            continue
        cycles, trunc = find_cycles(adj, comp)
        truncated_any |= trunc
        for cyc in cycles:
            cid += 1
            name = f"cyc-{cid:02d}"
            score, feats = score_cycle(cyc, edges, entities, volume, anchor)
            cycle_table[name] = {"nodes": cyc, "score": score}
            n = len(cyc)
            for i in range(n):
                for u in edges[(cyc[i], cyc[(i + 1) % n])]:
                    k = id(u)
                    if k not in best or score > best[k][0]:
                        best[k] = (score, feats, name)

    out = {}
    for u in units:
        uid = wire.hx(wire.unit_id(u["platform"], u["unit_no"]))
        score, feats, cyc = best.get(id(u), (0, {}, None))
        out[uid] = {
            "unitId": uid, "label": f"{u['platform']}:{u['unit_no']}",
            "evidenceScore": score, "features": feats, "cycleId": cyc,
            "reviewStatus": "PENDING_REVIEW" if score > cutoff else "NONE",
        }
    return out, {"cycles": cycle_table, "truncated": truncated_any}


def review(evidence: dict, decisions: dict) -> dict:
    """Apply a human reviewer's decisions {unitIdHex: 'EXCLUDED'|'CLEARED'} to held units."""
    out = {}
    for uid, e in evidence.items():
        d = decisions.get(uid)
        if d and e["reviewStatus"] != "PENDING_REVIEW":
            raise ValueError(f"{e['label']} is not pending review")
        if d and d not in ("EXCLUDED", "CLEARED"):
            raise ValueError(f"bad decision {d!r}")
        out[uid] = {**e, "reviewStatus": d or e["reviewStatus"]}
    return out


def evidence_doc(evidence: dict, cutoff: int = CUTOFF) -> dict:
    return {"cutoff": cutoff, "weights": WEIGHTS, "units": evidence}


# ------------------------------------------------------- entity resolution

def resolve_groups(entities: dict):
    """Union-find over strong links only: same PAN inside two GSTINs, shared director.
    Returns the frozen group map for the pool. Buyers absent from the map are their own group."""
    parent = {g: g for g in entities}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    reasons = {}  # signal -> members
    for g in entities:
        reasons.setdefault(("same_pan", pan_of(g)), []).append(g)
        for d in entities[g]["directors"]:
            reasons.setdefault(("shared_director", d), []).append(g)
    links = []
    for (kind, val), gs in sorted(reasons.items()):
        if len(gs) > 1:
            gs = sorted(gs)
            links.append(f"{kind}:{val}:{','.join(gs)}")
            for o in gs[1:]:
                parent[find(o)] = find(gs[0])

    comps = {}
    for g in entities:
        comps.setdefault(find(g), []).append(g)
    merged = sorted(sorted(c) for c in comps.values() if len(c) > 1)
    groups, ev = {}, {}
    for i, c in enumerate(merged, 1):
        gid = f"grp-{i:02d}"
        ev[gid] = {"members": c, "links": [l for l in links if any(m in l.split(":")[2].split(",") for m in c)]}
        for g in c:
            groups[wire.hx(wire.party_key(g))] = gid
    return {"groups": groups, "evidence": ev}
