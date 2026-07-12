#!/usr/bin/env python3
"""Genesis live dashboard generator.

Server-side: reads metrics_snapshots + topics + insights with the SERVICE key
(never exposed to the browser) and writes a self-contained static
dashboard.html (no JS, no keys). Cron regenerates it after each metrics run.

Env: SUPABASE_GENESIS_URL / SUPABASE_URL, SUPABASE_GENESIS_SERVICE_KEY / SUPABASE_SERVICE_KEY
Out: --out <path>  (default /opt/genesis/site/dist/dashboard/index.html)
"""
import os, sys, json, html, urllib.request, urllib.parse
from datetime import datetime, timezone

URL = os.environ.get("SUPABASE_GENESIS_URL") or os.environ.get("SUPABASE_URL") or "https://czzzdhzzvtewvhcrlryr.supabase.co"
KEY = os.environ.get("SUPABASE_GENESIS_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or ""
OUT = "/opt/genesis/site/dist/dashboard/index.html"
if "--out" in sys.argv:
    OUT = sys.argv[sys.argv.index("--out") + 1]


def rest(path, params, count=False):
    u = f"{URL}/rest/v1/{path}?" + urllib.parse.urlencode(params, safe=".=():,*")
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
    if count:
        h["Prefer"] = "count=exact"; h["Range"] = "0-0"
    req = urllib.request.Request(u, headers=h)
    r = urllib.request.urlopen(req, timeout=25)
    if count:
        cr = r.headers.get("Content-Range", "*/0")
        return int(cr.split("/")[-1])
    return json.load(r)


def latest_metrics():
    rows = rest("metrics_snapshots", {"select": "platform,metric_name,metric_value,captured_at",
                                      "order": "captured_at.desc", "limit": "3000"})
    seen = {}
    for x in rows:
        k = (x["platform"], x["metric_name"])
        if k not in seen:
            seen[k] = x["metric_value"]
    return seen


def num(v, dec=0):
    try:
        f = float(v)
        return f"{f:,.0f}" if dec == 0 else f"{f:,.{dec}f}"
    except Exception:
        return str(v)


# Модули, чей промпт генератор тянет с GitHub main в рантайме → merge доезжает
# до прода (замкнутый цикл). Остальные держат промпт inline (см. AUDIT #1).
LOOP_CLOSED = {"topic_distiller"}


def prompt_evolution():
    """Машинно-авторские версии промптов + вердикт испытания (keep/rollback).

    Это proof-of-autonomy: система сама переписала промпт (PR), сама измерила
    результат против реальных метрик, сама решила оставить/откатить.
    """
    rows = rest("prompts", {
        "select": "id,module,version,parent_version,approved_by,activated_at,is_active",
        "order": "activated_at.desc", "limit": "50"})
    machine = [r for r in rows
               if (r.get("approved_by") or "").startswith(("github:", "auto"))
               and r.get("parent_version")]
    # вердикты испытаний из insights (proposed_change.prompts_id → trial_*)
    ins = rest("insights", {"select": "proposed_change", "status": "eq.applied",
                            "order": "created_at.desc", "limit": "100"})
    verdict_by_pid = {}
    for i in ins:
        pc = i.get("proposed_change") or {}
        pid = pc.get("prompts_id")
        if pid:
            verdict_by_pid[pid] = (pc.get("trial_verdict"),
                                   pc.get("trial_change_pct"), pc.get("pr_url"))
    out = []
    for r in machine:
        v, pct, pr = verdict_by_pid.get(r["id"], (None, None, None))
        out.append({
            "module": r["module"], "version": r["version"],
            "parent": r["parent_version"], "by": r["approved_by"],
            "verdict": v, "pct": pct, "pr": pr,
            "closed": r["module"] in LOOP_CLOSED,
        })
    return out


def main():
    m = latest_metrics()
    g = lambda p, k, d=0: m.get((p, k), d)
    counts = {
        "signals": rest("trend_signals", {"select": "id"}, count=True),
        "topics": rest("topics", {"select": "id"}, count=True),
        "queued": rest("topics", {"select": "id", "status": "eq.queued"}, count=True),
        "insights": rest("insights", {"select": "id"}, count=True),
        "applied": rest("insights", {"select": "id", "status": "eq.applied"}, count=True),
    }
    # latest insight (the decision the agent made)
    ins = rest("insights", {"select": "insight_text,proposed_change,status,created_at",
                            "order": "created_at.desc", "limit": "1"})
    latest_ins = ins[0] if ins else {}
    pc = latest_ins.get("proposed_change") or {}
    decision = pc.get("hypothesis") or latest_ins.get("insight_text") or "—"
    pr_url = pc.get("pr_url") or ""

    platforms = [
        ("Instagram", "#E1306C", [("followers", num(g("instagram", "followers_count"))),
                                  ("reach / day", num(g("instagram", "account_reach_day"))),
                                  ("media", num(g("instagram", "media_count")))]),
        ("Telegram", "#2AABEE", [("subscribers", num(g("telegram", "channel_subscribers"))),
                                 ("posts", num(g("telegram", "channel_posts_total"))),
                                 ("posts / 24h", num(g("telegram", "channel_posts_24h")))]),
        ("YouTube", "#FF0000", [("avg view %", num(g("youtube", "avg_view_percentage"), 1)),
                                ("avg dur (s)", num(g("youtube", "avg_view_duration_sec"), 0)),
                                ("engagement %", num(float(g("youtube", "engagement_rate", 0)) * 100, 2))]),
        ("Facebook", "#1877F2", [("followers", num(g("facebook", "followers_count"))),
                                 ("posts / 7d", num(g("facebook", "posts_count_7d"))),
                                 ("reach 28d", num(g("facebook", "page_reach_28d")))]),
        ("Blog (Ghost)", "#F6B63B", [("posts", num(g("ghost", "posts_total"))),
                                     ("members", num(g("ghost", "newsletter_members_total")))]),
        ("Site (Plausible)", "#6C5CE7", [("visitors 7d", num(g("plausible", "site_visitors_7d"))),
                                         ("pageviews 7d", num(g("plausible", "site_pageviews_7d")))]),
    ]

    # --- prompt evolution timeline (proof-of-autonomy) ---
    evo = prompt_evolution()

    def evo_row(e):
        vmap = {
            "kept": ("KEPT", "#2ECC71"),
            "kept_watch": ("KEPT (watch)", "#c9a227"),
            "rolled_back": ("ROLLED BACK", "#E74C3C"),
            "rollback_pending": ("REGRESSED → revert pending", "#E67E22"),
            "pending": ("measuring…", "#8b96b0"),
            "kept_low_data": ("kept (low data)", "#8b96b0"),
            "insufficient_data": ("no metric", "#8b96b0"),
            "loop_open": ("inline — not measured", "#8b96b0"),
        }
        label, col = vmap.get(e["verdict"], ("measuring…", "#8b96b0"))
        pct = ""
        if e["pct"] not in (None, ""):
            try:
                pct = f' {float(e["pct"]):+.0f}%'
            except Exception:
                pct = ""
        loop = ('<span class="loopok">● loop closed</span>' if e["closed"]
                else '<span class="loopopen">○ inline (loop open)</span>')
        pr = (f'<a href="{html.escape(e["pr"])}">PR</a>'
              if (e["pr"] or "").startswith("http") else "")
        return (f'<div class="evo">'
                f'<span class="evomod">{html.escape(e["module"])}</span>'
                f'<span class="evover">v{html.escape(str(e["parent"]))} → '
                f'v{html.escape(str(e["version"]))}</span>'
                f'<span class="evobadge" style="color:{col};border-color:{col}">'
                f'{label}{pct}</span>{loop}'
                f'<span class="evopr">{pr}</span></div>')

    evo_html = "".join(evo_row(e) for e in evo) or '<div class="evo">—</div>'

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def card(name, color, rows):
        r = "".join(
            f'<div class="row"><span class="k">{html.escape(k)}</span>'
            f'<span class="v">{html.escape(str(v))}</span></div>' for k, v in rows)
        return (f'<div class="card"><div class="bar" style="background:{color}"></div>'
                f'<div class="pname">{html.escape(name)}</div>{r}</div>')

    cards = "".join(card(n, c, r) for n, c, r in platforms)

    big = lambda v, l, col="#F6B63B": (
        f'<div class="big"><div class="bignum" style="color:{col}">{v}</div>'
        f'<div class="biglbl">{l}</div></div>')

    doc = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Genesis Content OS — live telemetry</title>
<style>
:root{{--bg:#0A0E1A;--card:#121a2e;--gold:#F6B63B;--tx:#e8ecf5;--mut:#8b96b0}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(1200px 600px at 70% -10%,#16233f 0,var(--bg) 60%);
color:var(--tx);font:15px/1.5 -apple-system,Inter,Segoe UI,Roboto,sans-serif;padding:32px}}
.wrap{{max-width:1080px;margin:0 auto}}
h1{{margin:0;font-size:26px;letter-spacing:.5px}}
h1 b{{color:var(--gold)}}
.sub{{color:var(--mut);margin:4px 0 26px}}
.live{{color:#2ECC71;font-weight:600}}
.bigs{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:26px}}
.big{{background:var(--card);border:1px solid #1e2a44;border-radius:14px;padding:16px 20px;flex:1;min-width:150px}}
.bignum{{font-size:30px;font-weight:800}}
.biglbl{{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin-top:2px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}}
.card{{background:var(--card);border:1px solid #1e2a44;border-radius:14px;padding:16px 18px 14px;position:relative;overflow:hidden}}
.bar{{position:absolute;top:0;left:0;right:0;height:3px}}
.pname{{font-weight:700;margin:4px 0 10px}}
.row{{display:flex;justify-content:space-between;padding:5px 0;border-top:1px solid #1a2embed}}
.row{{border-top:1px solid #1a2540}}
.k{{color:var(--mut)}} .v{{font-weight:700}}
.decision{{background:var(--card);border:1px solid #2a3860;border-left:3px solid var(--gold);
border-radius:12px;padding:16px 20px;margin:26px 0}}
.decision .lbl{{color:var(--gold);font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}}
.decision a{{color:#7db3ff}}
.loop{{color:var(--mut);font-size:13px;margin:18px 0}}
.loop b{{color:var(--tx)}}
.evotitle{{color:var(--gold);font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin:26px 0 10px}}
.evowrap{{background:var(--card);border:1px solid #1e2a44;border-radius:14px;padding:6px 4px}}
.evo{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 16px;border-top:1px solid #1a2540;font-size:13px}}
.evo:first-child{{border-top:none}}
.evomod{{font-weight:700;min-width:120px}}
.evover{{color:var(--mut);font-family:ui-monospace,Menlo,monospace}}
.evobadge{{border:1px solid;border-radius:20px;padding:2px 10px;font-weight:700;font-size:12px}}
.loopok{{color:#2ECC71;font-size:12px}}
.loopopen{{color:#c9a227;font-size:12px}}
.evopr{{margin-left:auto}} .evopr a{{color:#7db3ff;text-decoration:none}}
.foot{{color:var(--mut);font-size:12px;margin-top:28px;border-top:1px solid #1a2540;padding-top:14px;
display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}}
.foot a{{color:var(--gold);text-decoration:none}}
</style></head><body><div class="wrap">
<h1>GENESIS <b>CONTENT OS</b> — live telemetry</h1>
<div class="sub">Autonomous content engine that learns from its own audience · <span class="live">● live</span> · updates every 6h</div>

<div class="bigs">
{big(num(counts['signals']),'trend signals scanned')}
{big(num(counts['queued']),'topics queued','#8b7bff')}
{big(num(counts['insights']),'insights learned','#E1306C')}
{big(num(counts['applied']),'prompt changes applied','#2ECC71')}
</div>

<div class="loop">🔁 <b>self-improving loop:</b> scan → distill → render → publish → measure → <b>insight</b> → <b>auto-PR</b> → repeat</div>

<div class="grid">{cards}</div>

<div class="decision">
<div class="lbl">latest decision the agent made</div>
{html.escape(str(decision))[:280]}
{'<br><a href="'+html.escape(pr_url)+'">→ view the pull request</a>' if pr_url.startswith('http') else ''}
</div>

<div class="evotitle">prompt evolution — the system rewrites itself, then reality judges it</div>
<div class="evowrap">{evo_html}</div>

<div class="foot">
<span>updated {updated} · data: Supabase (read-only)</span>
<a href="https://github.com/DenisShokhirev041279/genesis-content-os">★ Genesis OS on GitHub</a>
</div>
</div></body></html>"""

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[dashboard] wrote {OUT} ({len(doc)} bytes) · "
          f"signals={counts['signals']} insights={counts['insights']}")


if __name__ == "__main__":
    main()
