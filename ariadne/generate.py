"""Seeded synthetic-data generator. All data is synthetic; GSTINs are well-formed but fictional.

Seeds: A = demo (every planted scenario), B = unseen (same generator, different seed),
C = adversarial (rings and groups built to beat the heuristics).

All dates are relative to one anchor date so the hero invoice stays valid on any demo day.
"""
import csv
import json
import random
import string
from datetime import date, timedelta
from pathlib import Path

ANCHOR = date(2026, 9, 29)
STATES = ["33", "27", "29", "07", "24", "36", "19", "06"]
FIELDS = ["platform", "unit_no", "invoice_no", "seller_gstin", "buyer_gstin", "amount_paise",
          "invoice_date", "due_date", "financier", "rediscount_path", "reserve"]


def _r(rng, n, alphabet):
    return "".join(rng.choice(alphabet) for _ in range(n))


class World:
    def __init__(self, rng, anchor):
        self.rng, self.anchor = rng, anchor
        self.entities, self.rows = {}, []
        self.n_dir = self.n_addr = self.n_bank = 0
        self.pans = set()

    def _pan(self):
        while True:
            p = _r(self.rng, 5, string.ascii_uppercase) + _r(self.rng, 4, string.digits) + _r(self.rng, 1, string.ascii_uppercase)
            if p not in self.pans:
                self.pans.add(p)
                return p

    def entity(self, name, state=None, pan=None, years=None, days=None, directors=None, address=None, gstin=None,
               rating="A"):
        rng = self.rng
        pan = pan or self._pan()
        g = gstin or f"{state or rng.choice(STATES)}{pan}1Z{_r(rng, 1, string.digits + string.ascii_uppercase)}"
        while g in self.entities:
            g = g[:12] + "2Z" + g[14:]
        if directors is None:
            self.n_dir += 1
            directors = [f"DIN{self.n_dir:08d}"]
        if address is None:
            self.n_addr += 1
            address = f"addr-{self.n_addr:05d}"
        self.n_bank += 1
        reg = self.anchor - timedelta(days=days if days is not None else (years or rng.randint(6, 20)) * 365 + rng.randint(0, 200))
        self.entities[g] = {"name": name, "registered": reg.isoformat(), "directors": directors,
                            "address": address, "bank": f"bank-{self.n_bank:06d}", "rating": rating}
        return g

    def amount(self, lo=150_000, hi=900_000):
        return self.rng.randint(lo, hi) * 100 + self.rng.randint(1, 99)  # paise, never round

    def add(self, seller, buyer, amount, age, tenor, financier="B", path="", reserve=0, tag="bg"):
        inv = self.anchor - timedelta(days=age)
        self.rows.append({"seller_gstin": seller, "buyer_gstin": buyer, "amount_paise": amount,
                          "invoice_date": inv, "due_date": inv + timedelta(days=tenor),
                          "financier": financier, "rediscount_path": path, "reserve": reserve, "tag": tag})
        return self.rows[-1]


def make(seed: str, anchor: date = ANCHOR):
    rng = random.Random(f"ariadne:{seed}")
    hard = seed == "C"
    w = World(rng, anchor)
    truth = {"seed": seed, "anchor": anchor.isoformat(), "fabricated_loop": [], "legit_loop": [],
             "groups": [], "rediscounted": [], "reserve": {}}

    sellers = [w.entity(f"Seller {i}") for i in range(60)]
    buyers = [w.entity(f"Buyer {i}", rating="BBB" if i in (3, 17, 29) else rng.choice(["AAA", "AA", "AA-", "A+", "A", "A-"]))
              for i in range(40)]
    hero_s = w.entity("Chennai MSME", gstin="33AAAPL1234C1Z5", pan="AAAPL1234C", years=9)
    hero_b = w.entity("Karnataka Buyer", gstin="29BBBCM5678D1Z2", pan="BBBCM5678D", years=14, rating="AA")
    w.pans |= {"AAAPL1234C", "BBBCM5678D"}
    sellers.append(hero_s)
    buyers.append(hero_b)

    # ---- background: eligible, short-left, long-tenor, defaulter candidates, reserve
    def bg(n, tenor, age, **kw):
        return [w.add(rng.choice(sellers), rng.choice(buyers), w.amount(),
                      age() if callable(age) else age, tenor() if callable(tenor) else tenor, **kw)
                for _ in range(n)]

    regular = bg(112, lambda: rng.randint(75, 90), lambda: rng.randint(5, 30))
    bg(8, 120, 20, tag="tenor_over")
    bg(8, 60, 50, tag="maturing")
    bg(3, 60, lambda: rng.randint(40, 44), tag="defaulter_candidate")
    bg(10, 90, lambda: rng.randint(5, 20), reserve=1, tag="reserve")
    # three re-discounts (B>C>B): over the two-transfer limit, so R6 must refuse it
    w.add(rng.choice(sellers), rng.choice(buyers[:3] + buyers[4:]), w.amount(), 12, 85, financier="A", path="B>C>B",
          tag="transfers3")
    hero = w.add(hero_s, hero_b, 48_500_000, 58, 90, financier="A", path="B", tag="hero")
    # rediscount chain: hero + 20 A>B, 5 of them go on B>C (S5/S6)
    for u in rng.sample(regular, 25):
        u["financier"], u["rediscount_path"] = "A", "B>C" if len(truth["rediscounted"]) < 5 else "B"
        truth["rediscounted"].append(u)
    truth["hero"] = hero

    # ---- S1 fabricated loop (5 entities, circle)
    if hard:
        ring = [w.entity(f"Ring {i}", state="33", years=rng.randint(7, 15)) for i in range(5)]
        weights = [1.0, 0.55, 0.3, 0.7, 0.4]
        amts = [int(600_000 * x) * 100 + rng.randint(1, 99) for x in weights]
        step, start, tenor = 12, 55, 90
    else:
        d1, d2 = "DIN90000001", "DIN90000002"
        ring = [w.entity(f"Ring {i}", state="33", days=(200 if i < 2 else rng.randint(8, 15) * 365),
                         directors=[[d1], [d1], [d1, d2], [d2], [d2]][i]) for i in range(5)]
        amts = [round(600_000 * (1 + rng.uniform(-0.01, 0.01)), -3) for _ in range(5)]
        amts = [int(a) * 100 for a in amts]
        step, start, tenor = 1, 25, 60
    for i in range(5):
        u = w.add(ring[i], ring[(i + 1) % 5], amts[i], start - i * step, tenor, tag="s1")
        truth["fabricated_loop"].append(u)
    truth["groups"].append(ring if not hard else [])

    # ---- S2 legitimate loop (steel mill and fabricator, old, very different amounts)
    mill = w.entity("Steel Mill", state="33", years=18)
    fab = w.entity("Fabricator", state="33", years=15)
    for age, amt in [(12, 1_000_000), (50, 800_000)]:
        truth["legit_loop"].append(w.add(mill, fab, amt * 100 + rng.randint(1, 99), age, 90, tag="s2"))
    for age, amt in [(30, 230_000), (70, 190_000)]:
        truth["legit_loop"].append(w.add(fab, mill, amt * 100 + rng.randint(1, 99), age, 90, tag="s2"))
    for seller in (mill, fab):
        for _ in range(3):
            w.add(seller, rng.choice(buyers), w.amount(300_000, 700_000), rng.randint(5, 25), 90, tag="s2_outside")

    # ---- totals, then S3 (30%) and S4 PSU (15%) sized against the whole
    t0 = sum(r["amount_paise"] for r in w.rows)
    total = int(t0 / 0.55)
    if hard:  # linked only by one shared registered address: a weak signal that must not auto-merge
        shared = "addr-shared-9000"
        grp = [w.entity(f"Group {i}", state=rng.choice(STATES), address=shared) for i in range(4)]
    else:
        pan = w._pan()
        g1 = w.entity("Group 1", state="33", pan=pan, directors=["DIN80000001"])
        g2 = w.entity("Group 2", state="27", pan=pan, directors=["DIN80000002", "DIN80000009"])
        g3 = w.entity("Group 3", state="29", directors=["DIN80000009"])
        g4 = w.entity("Group 4", state="07", directors=["DIN80000009"])
        grp = [g1, g2, g3, g4]
        w.entities[g4]["bank"] = w.entities[g1]["bank"]  # a third strong link: the same bank account
    truth["groups"].append(grp)  # a true group in every seed, even when only a weak signal links it
    per_buyer = int(total * 0.30) // 4
    for g in grp:
        ws = [rng.uniform(0.5, 1.5) for _ in range(6)]
        for x in ws:
            w.add(rng.choice(sellers), g, int(per_buyer * x / sum(ws)), rng.randint(5, 30), rng.randint(75, 90), tag="s3")
    late = w.add(rng.choice(sellers), grp[0], int(total * 0.065), 5, 90, reserve=1, tag="s3_late")

    psu = w.entity("State PSU", state="07", years=35)
    ws = [rng.uniform(0.5, 1.5) for _ in range(20)]
    for x in ws:
        w.add(rng.choice(sellers), psu, int(total * 0.15 * x / sum(ws)), rng.randint(5, 30), rng.randint(75, 90), tag="s4")
    truth["psu"], truth["late"] = psu, late

    # two firms whose only link is a shared registered address and near-identical names:
    # a medium signal, so it becomes a candidate for a human to confirm, never an auto-merge
    twins = [w.entity("Twin Traders Pvt Ltd", state="33", address="addr-twin-7000"),
             w.entity("Twin Traders Private Limited", state="27", address="addr-twin-7000")]
    for tw in twins:
        ws = [rng.uniform(0.5, 1.5) for _ in range(5)]
        for x in ws:
            w.add(rng.choice(sellers), tw, int(total * 0.03 * x / sum(ws)), rng.randint(5, 30), rng.randint(75, 90), tag="twin")
    truth["groups"].append(twins)
    truth["candidates"] = [twins]

    # ---- platforms, unit numbers, invoice numbers (deterministic order)
    rng.shuffle(w.rows)
    n, inv = 0, 4000
    for r in w.rows:
        inv += 1
        r["invoice_no"] = f"INV-{inv:05d}"
        if r is hero:
            r["platform"], r["unit_no"] = "P1", "FU-000183"
            continue
        n += 1
        n += n == 183
        r["platform"], r["unit_no"] = rng.choice(["P1", "P2", "P3"]), f"FU-{n:06d}"

    def label(r): return f"{r['platform']}:{r['unit_no']}"
    t = {k: ([label(u) for u in v] if k in ("fabricated_loop", "legit_loop", "rediscounted") else v)
         for k, v in truth.items()}
    t["hero"], t["late"] = label(hero), label(late)
    t["stale_owner_unit"] = t["rediscounted"][0]
    t["groups"] = [sorted(g) for g in truth["groups"] if g]
    t["candidates"] = [sorted(c) for c in truth["candidates"]]
    t["reserve"] = [label(r) for r in w.rows if r["reserve"]]
    t["counts"] = {"units": len(w.rows), "entities": len(w.entities)}
    return w, t


def write(seed: str, out: Path, anchor: date = ANCHOR):
    w, truth = make(seed, anchor)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "units.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(w.rows)
    with open(out / "entities.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["gstin", "name", "pan", "registered", "directors", "address", "bank", "rating"])
        for g, e in w.entities.items():
            wr.writerow([g, e["name"], g[2:12], e["registered"], "|".join(e["directors"]), e["address"], e["bank"],
                         e["rating"]])
    (out / "truth.json").write_text(json.dumps(truth, indent=1, default=str))
    (out / "meta.json").write_text(json.dumps({"seed": seed, "anchor": anchor.isoformat(), "synthetic": True}))
    return truth


def load(d: Path):
    """units (typed rows), entities, meta, truth."""
    units = []
    for r in csv.DictReader(open(d / "units.csv")):
        r["amount_paise"], r["reserve"] = int(r["amount_paise"]), int(r["reserve"])
        r["invoice_date"], r["due_date"] = date.fromisoformat(r["invoice_date"]), date.fromisoformat(r["due_date"])
        units.append(r)
    entities = {}
    for r in csv.DictReader(open(d / "entities.csv")):
        entities[r["gstin"]] = {"name": r["name"], "registered": r["registered"],
                                "directors": r["directors"].split("|"), "address": r["address"], "bank": r["bank"],
                                "rating": r["rating"]}
    return units, entities, json.loads((d / "meta.json").read_text()), json.loads((d / "truth.json").read_text())
