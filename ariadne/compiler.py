"""Layer 4 without an LLM: a deterministic prospectus parser.

This build has no language model. It stands in for the spec's AI slot with a closed phrase grammar
over the closed rule vocabulary. It keeps every guarantee the spec asks of the compiler:
  - it only proposes; a human approves, edits or rejects each rule and signs the set;
  - every rule carries a sourceSpan that must appear verbatim in the input text;
  - anything it cannot map goes to `unsupported`, never silently dropped;
  - values must fall inside sane bounds;
  - it never sees ledger data and never decides whether a unit is eligible.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import engine
from .chain import ROOT

SAMPLE = ROOT / "compiler" / "sample-prospectus.txt"
F = re.IGNORECASE


def _pct_to_bps(x):
    v = Decimal(x) * 100
    if v != v.to_integral_value():
        raise ValueError(f"{x}% is not a whole number of basis points")
    return int(v)


# kind -> (patterns, value extractor, bounds check)
GRAMMAR = [
    ("state_required", [r"\bmust be (financed|registered|pooled)\b"], lambda m: m.group(1).upper(),
     lambda v: v in ("FINANCED", "REGISTERED", "POOLED")),
    ("tenor_max_days", [r"(?:maximum|longest)\s+(?:invoice\s+)?tenor\s+(?:is|of|shall be)?\s*(\d+)\s*days?",
                        r"tenor\b.{0,25}?(?:not exceed|at most|no (?:more|longer) than)\s*(\d+)\s*days?"],
     lambda m: int(m.group(1)), lambda v: 1 <= v <= 365),
    ("min_days_to_maturity", [r"at least (\d+) days?\s+(?:remaining|left)", r"(\d+) days? or more (?:remaining|left)"],
     lambda m: int(m.group(1)), lambda v: 0 <= v <= 365),
    ("max_transfer_count", [r"(?:no more than|at most|not more than) (\d+) (?:transfers?|re-?discounts?)"],
     lambda m: int(m.group(1)), lambda v: 0 <= v <= 20),
    ("buyer_rating_min", [r"buyer rating (?:must be|of|at least)\s+([A-D]{1,3}[+-]?)(?: or better)?"],
     lambda m: m.group(1).upper().replace("BBB-", "BBB-"), lambda v: v in engine.RATINGS),
    ("evidence_score_max", [r"maximum (?:integrity )?(?:ring |evidence )?score is (\d+)",
                            r"(?:ring |evidence )?score\b.{0,20}?(?:may not|must not|shall not) exceed (\d+)"],
     lambda m: int(m.group(1)), lambda v: 0 <= v <= 1000),
    ("buyer_group_concentration_max_bps",
     [r"(?:resolved )?(?:buyer|obligor) group\b.{0,30}?(?:exceed|above)\s+(\d+(?:\.\d+)?)\s*(?:percent|per cent|%)"],
     lambda m: _pct_to_bps(m.group(1)), lambda v: 1 <= v <= 10_000),
    ("seller_concentration_max_bps",
     [r"seller concentration\b.{0,30}?(?:exceed|above)\s+(\d+(?:\.\d+)?)\s*(?:percent|per cent|%)"],
     lambda m: _pct_to_bps(m.group(1)), lambda v: 1 <= v <= 10_000),
    ("min_pool_size", [r"at least (\d+) (?:live |eligible )?units?"], lambda m: int(m.group(1)),
     lambda v: 1 <= v <= 100_000),
]

AMBIGUITIES = [
    (r"\bexposure\b", "'Exposure' is not defined. This build measures concentration on face value; confirm the basis."),
    (r"outstanding principal", "'Outstanding principal' versus face value: this build uses face value; confirm the basis."),
    (r"\bring score\b|\bintegrity\b", "The document says 'ring score'. It is mapped to the evidence score, which is evidence for a "
                                        "human reviewer, not a fraud verdict."),
    (r"\blive\b", "'Live' is read as pooled and not yet settled or defaulted. The size check runs once, when the pool is sealed."),
    (r"\badmitted\b", "'Admitted' is read as the moment the unit is added to the pool."),
    (r"buyer rating", "Ratings come from a synthetic scale and travel with the pinned evidence set."),
]


def read_back(kind, v):
    pct = (lambda: f"{v / 100:g}%") if isinstance(v, int) else (lambda: str(v))
    return {
        "state_required": lambda: f"A unit must be {v} when it is added to the pool.",
        "tenor_max_days": lambda: f"The invoice's original tenor may be at most {v} days.",
        "min_days_to_maturity": lambda: f"At least {v} days must remain to maturity when the unit is pooled.",
        "max_transfer_count": lambda: f"A unit may have been re-discounted at most {v} times.",
        "buyer_rating_min": lambda: f"The buyer must be rated {v} or better.",
        "evidence_score_max": lambda: f"A unit's evidence score may be at most {v}, unless a reviewer clears it.",
        "buyer_group_concentration_max_bps": lambda: f"No resolved buyer group may be more than {pct()} of the pool's face value.",
        "seller_concentration_max_bps": lambda: f"No single seller may be more than {pct()} of the pool's face value.",
        "min_pool_size": lambda: f"The pool must hold at least {v} units when it is sealed.",
    }[kind]()


def clauses(text: str):
    return [c.strip() for c in re.split(r"(?<=[.;])\s+|\n+", text) if c.strip()]


def parse(text: str) -> dict:
    """Propose rules. Nothing here is final: every rule starts PENDING and needs a human."""
    rules, unsupported = [], []
    for span in clauses(text):
        hit = None
        for kind, patterns, extract, ok in GRAMMAR:
            for p in patterns:
                m = re.search(p, span, F)
                if m:
                    try:
                        v = extract(m)
                    except (ValueError, InvalidOperation) as e:
                        hit = ("bad", kind, str(e))
                        break
                    hit = ("ok", kind, v, ok(v))
                    break
            if hit:
                break
        if not hit:
            unsupported.append({"span": span, "reason": "No rule in the closed vocabulary matches this clause."})
        elif hit[0] == "bad" or not hit[3]:
            unsupported.append({"span": span, "reason": f"Matched {hit[1]} but the value is outside sane bounds."
                                                        if hit[0] == "ok" else f"Matched {hit[1]}: {hit[2]}"})
        else:
            kind, v = hit[1], hit[2]
            rule = {"id": f"R{len(rules) + 1}", "kind": kind, "value": v, "sourceSpan": span,
                    "readBack": read_back(kind, v), "status": "PENDING",
                    "ambiguities": [msg for pat, msg in AMBIGUITIES if re.search(pat, span, F)]}
            if kind == "buyer_group_concentration_max_bps":
                rule["basis"] = "resolved_group"
            rules.append(rule)
    return {"rules": rules, "unsupported": unsupported, "aiUsed": False}


def validate(rules, text):
    """Deterministic checks on whatever the reviewer sends back. Raises ValueError."""
    known = {k: ok for k, _, _, ok in GRAMMAR}
    for r in rules:
        if r["kind"] not in known:
            raise ValueError(f"{r['id']}: kind {r['kind']!r} is not in the vocabulary")
        if r["sourceSpan"] not in text:
            raise ValueError(f"{r['id']}: source words are not in the document")
        if not known[r["kind"]](r["value"]):
            raise ValueError(f"{r['id']}: value {r['value']!r} is outside sane bounds")


def to_ruleset(rules, unsupported, text, name, signed_at=None) -> dict:
    """Approved rules only, renumbered, ready for engine.sign_ruleset."""
    approved = [r for r in rules if r.get("status") in ("APPROVED", "EDITED")]
    validate(approved, text)
    out, clauses_ = [], {}
    for i, r in enumerate(approved, 1):
        item = {"id": f"R{i}", "kind": r["kind"], "value": r["value"]}
        if r.get("basis"):
            item["basis"] = r["basis"]
        out.append(item)
        clauses_[item["id"]] = r["sourceSpan"]
    return {"ruleSetId": name, "version": 1, "rules": out, "sourceClauses": clauses_,
            "unsupported": [u["span"] for u in unsupported],
            "reviewedAt": signed_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def sample_text() -> str:
    return SAMPLE.read_text(encoding="utf-8")
