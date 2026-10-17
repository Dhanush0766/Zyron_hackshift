"""Printable doctor report (MedGate addition). Server-rendered HTML; the browser's Print
dialog saves it as PDF. Every figure comes from `Engine.doctor_report` (stored records)."""
from __future__ import annotations

from datetime import datetime
from html import escape


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _dt(t) -> str:
    return datetime.fromtimestamp(t).strftime("%d %b %Y %H:%M") if t else "—"


def _spark(points: list, lo: float, hi: float, limit: float = None, color: str = "#0f766e") -> str:
    """Inline SVG line chart; `limit` draws the gate threshold as a dashed line."""
    pts = [p for p in points if p is not None]
    if len(pts) < 2:
        return '<div class="muted">Not enough readings to chart.</div>'
    W, H, pad = 640, 140, 8
    span = (hi - lo) or 1

    def y(v):
        return pad + (H - 2 * pad) * (1 - (min(max(v, lo), hi) - lo) / span)

    step = (W - 2 * pad) / (len(pts) - 1)
    path = " ".join(f"{'M' if i == 0 else 'L'}{pad + i * step:.1f},{y(v):.1f}" for i, v in enumerate(pts))
    lim = (f'<line x1="{pad}" x2="{W - pad}" y1="{y(limit):.1f}" y2="{y(limit):.1f}" stroke="#b91c1c" '
           f'stroke-dasharray="5 4" stroke-width="1.5"/><text x="{W - pad}" y="{y(limit) - 4:.1f}" text-anchor="end" '
           f'font-size="11" fill="#b91c1c">gate limit {limit:g}</text>') if limit is not None else ""
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="trend chart">'
            f'<rect x="0" y="0" width="{W}" height="{H}" fill="#f8fafc" rx="6"/>{lim}'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<text x="{pad}" y="{H - 4}" font-size="11" fill="#64748b">oldest</text>'
            f'<text x="{W - pad}" y="{H - 4}" font-size="11" fill="#64748b" text-anchor="end">newest</text></svg>')


def _sumrow(label, s, unit):
    if not s:
        return f"<tr><td>{label} ({unit})</td><td colspan=4 class=muted>no valid readings</td></tr>"
    return (f"<tr><td>{label} ({unit})</td><td>{s['min']:g}</td><td>{s['avg']:g}</td>"
            f"<td>{s['max']:g}</td><td>{s['n']}</td></tr>")


def _rules(m) -> str:
    parts = [_e("%s %s %g" % (x["field"], x["op"], x["value"])) for x in m["rules"]]
    if m["requires_meal"]:
        parts.append("no meal")
    return "; ".join(parts)


def render_report(r: dict) -> str:
    dev, v = r["device"], r["vitals"]
    series = v["series"]
    by = r["by_state"]
    adher = "—" if r["adherence_pct"] is None else f"{r['adherence_pct']}%"
    meds = "".join(
        f"<tr><td>Box {m['compartment']}</td><td>{_e(m['name'])}</td><td>{_e(m['drug_class_label'])}</td>"
        f"<td>{_rules(m)}</td>"
        f"<td>{m['min_interval_h']:g} h / {m['late_window_min']:g} min</td>"
        f"<td>{m['per_med'].get('COLLECTED', 0)} / {m['per_med'].get('MISSED', 0)} / "
        f"{m['per_med'].get('WITHHELD', 0) + m['per_med'].get('HELD', 0)}</td><td>{m['stock']}</td></tr>"
        for m in r["medications"])
    holds = "".join(
        f"<tr><td>{_dt(h['due_at'])}</td><td>{_e(h['medication'])}</td><td>{'<br>'.join(_e(x) for x in h['reasons'])}</td>"
        f"<td>{_e(h['state'])}{(' · ' + _e(h['reviewer'])) if h['reviewer'] else ''}</td></tr>" for h in r["holds"]) \
        or "<tr><td colspan=4 class=muted>No doses were held by the health gate in this period.</td></tr>"
    trends = "".join(f"<li><b>{_e(t['kind'])}</b>: {_e(t['message'])} <span class=muted>({_dt(t['created_at'])}"
                     f"{', resolved' if t['resolved_at'] else ', open'})</span></li>" for t in r["trends"]) \
        or "<li class=muted>No sustained SpO₂ or BP drift detected.</li>"
    alerts = "".join(f"<li>{_e(a['kind'])}: {_e(a['message'])} <span class=muted>{_dt(a['created_at'])}"
                     f"{' · resolved by ' + _e(a['resolved_by']) if a['resolved_at'] else ' · open'}</span></li>"
                     for a in r["alerts"] if not a["kind"].startswith("TREND_")) or "<li class=muted>None.</li>"
    overrides = "".join(f"<li>{_dt(o['due_at'])} · {_e(o['medication'])} · {_e(o['state'])} by <b>{_e(o['reviewer'])}</b>"
                        f"{(' · ' + _e(o['review_note'])) if o['review_note'] else ''}</li>" for o in r["overrides"]) \
        or "<li class=muted>None.</li>"
    audit = r["audit"]
    sim = '<span class="sim">SIMULATOR DATA</span>' if dev["kind"] == "SIMULATOR" else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MedGate report · {_e(dev['patient_name'] or dev['id'])}</title>
<style>
body{{font-family:"Segoe UI",system-ui,sans-serif;color:#0f172a;max-width:900px;margin:24px auto;padding:0 16px;font-size:14px}}
h1{{font-size:22px;margin:0}} h2{{font-size:16px;margin:22px 0 8px;border-bottom:1px solid #e2e8f0;padding-bottom:4px}}
table{{width:100%;border-collapse:collapse}} td,th{{text-align:left;padding:5px 6px;border-bottom:1px solid #f1f5f9;vertical-align:top}}
th{{font-size:12px;color:#475569}} .muted{{color:#64748b}} .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}}
.k{{border:1px solid #e2e8f0;border-radius:8px;padding:10px}} .k b{{display:block;font-size:22px}}
.sim{{background:#7c3aed;color:#fff;border-radius:4px;padding:2px 6px;font-size:11px;margin-left:6px}}
.note{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:8px 10px;font-size:12.5px;margin-top:14px}}
button{{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid #0f766e;background:#0f766e;color:#fff;cursor:pointer}}
@media print{{button{{display:none}} body{{margin:0}}}} @media(max-width:600px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body>
<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
<div><h1>MedGate adherence &amp; vitals report {sim}</h1>
<div class="muted">Patient: <b>{_e(dev['patient_name'] or '—')}</b> · Device {_e(dev['id'])} · last {r['days']} days · generated {_dt(r['generated_at'])}</div></div>
<button onclick="window.print()">Print / save PDF</button></div>
<div class="kpis"><div class="k"><span class="muted">Adherence</span><b>{adher}</b><span class="muted">{by.get('COLLECTED', 0)} taken / {by.get('MISSED', 0)} missed</span></div>
<div class="k"><span class="muted">Held (not released)</span><b>{len(r['holds'])}</b><span class="muted">doses not released</span></div>
<div class="k"><span class="muted">Named overrides</span><b>{len(r['overrides'])}</b><span class="muted">reviewer decisions</span></div>
<div class="k"><span class="muted">Audit chain</span><b style="color:{'#15803d' if audit['ok'] else '#b91c1c'}">{'Intact' if audit['ok'] else 'BROKEN'}</b><span class="muted">{audit['records']} records</span></div></div>
<h2>Medicines and their gate rules</h2>
<table><tr><th>Box</th><th>Medicine</th><th>Class</th><th>Held if</th><th>Min gap / late window</th><th>Taken / missed / held</th><th>Stock</th></tr>{meds}</table>
<h2>Vital signs (valid readings only)</h2>
<table><tr><th></th><th>Min</th><th>Average</th><th>Max</th><th>Readings</th></tr>
{_sumrow('SpO₂', v['spo2'], '%')}{_sumrow('Systolic', v['bp_sys'], 'mmHg')}{_sumrow('Diastolic', v['bp_dia'], 'mmHg')}{_sumrow('Pulse', v['hr'], 'bpm')}</table>
<p class="muted" style="margin:10px 0 4px">SpO₂ over time</p>{_spark([x['spo2'] for x in series], 85, 100, 94)}
<p class="muted" style="margin:10px 0 4px">Systolic BP over time</p>{_spark([x['bp_sys'] for x in series], 80, 190, 160, '#1d4ed8')}
<h2>Early warnings (drift while doses still passed)</h2><ul>{trends}</ul>
<h2>Doses held (health gate, drug rules, double-dose guard)</h2><table><tr><th>When</th><th>Medicine</th><th>Why</th><th>Outcome</th></tr>{holds}</table>
<h2>Human overrides</h2><ul>{overrides}</ul>
<h2>Other alerts (refill, storage, patient card)</h2><ul>{alerts}</ul>
<div class="note">Prototype report. Gate thresholds are IDF-B example values and drug hold parameters are demonstration values, not clinical guidance.
"Taken" means the tray was emptied while a hand was detected, not that the tablet was swallowed. All figures come from the device's stored, hash-chained records.</div>
</body></html>"""
