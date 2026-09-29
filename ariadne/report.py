"""One-page HTML trustee report from a verifier result."""
from html import escape
from datetime import datetime, timezone

CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.4rem;margin:0}.sub{color:#666;margin-bottom:1rem}.banner{padding:.8rem 1rem;border-radius:6px;font-weight:600;margin:1rem 0}
.ok{background:#e6f4ea;color:#137333}.bad{background:#fce8e6;color:#c5221f}table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}
td,th{border-bottom:1px solid #e5e5e5;padding:.35rem .5rem;text-align:left;vertical-align:top;font-size:14px}
.PASS{color:#137333;font-weight:600}.FAIL{color:#c5221f;font-weight:600}.INFO{color:#555}code{font-size:13px}
.syn{background:#fff8e1;padding:.4rem .7rem;border-radius:4px;font-size:13px}"""


def render(r: dict) -> str:
    ok = r["status"] == "VERIFIED"
    p = r["performance"]
    rows = "".join(f"<tr><td><code>{n}</code></td><td class={c['status']}>{c['status']}</td><td>{escape(c['detail'])}</td></tr>"
                   for n, c in r["checks"].items())
    flags = "".join(f"<tr><td>{escape(f['check'])}</td><td>{escape(f['unit'])}</td><td>{escape(f['rule'])} {escape(f['kind'])}</td>"
                    f"<td>{escape(f['detail'])}</td><td>{f['block']}</td></tr>" for f in r["failures"])
    flags = (f"<h2>Flagged units</h2><table><tr><th>Check</th><th>Unit</th><th>Rule</th><th>Reason</th><th>Block</th></tr>{flags}</table>"
             if flags else "<h2>Flagged units</h2><p>None.</p>")
    tl = "".join(f"<tr><td>{t['block']}</td><td>{datetime.fromtimestamp(t['ts'], timezone.utc):%Y-%m-%d %H:%M}</td>"
                 f"<td>{t['op']}</td><td>{escape(t['unit'])}</td><td>v{t['version']}</td></tr>" for t in r["timeline"])
    return f"""<!doctype html><meta charset=utf-8><title>Ariadne trustee report</title><style>{CSS}</style>
<h1>Ariadne pool verification report</h1><div class=sub>Pool <b>{escape(r['pool'])}</b> · manifest v{r['version']} · ledger <code>{r['ledger']}</code></div>
<div class=syn>SYNTHETIC DATA. Recomputed independently from chain events, the signed rule set and the frozen buyer-group map.</div>
<div class="banner {'ok' if ok else 'bad'}">{r['status']}{'' if ok else ': ' + ', '.join(r['failed'])}</div>
<h2>Checks</h2><table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>{rows}</table>{flags}
<h2>Performance</h2><p>Settled {p['settled']} (₹{p['settledPaise'] / 100:,.0f}) · defaulted {p['defaulted']} (₹{p['defaultedPaise'] / 100:,.0f}) · outstanding {p['outstanding']} (₹{p['outstandingPaise'] / 100:,.0f})</p>
<h2>Membership timeline ({len(r['timeline'])} events)</h2><table><tr><th>Block</th><th>Time (UTC)</th><th>Event</th><th>Unit</th><th>Manifest</th></tr>{tl}</table>
<p class=sub>Not a claim about real goods, fraud or credit risk. Loop and group findings are evidence for a human reviewer.</p>"""
