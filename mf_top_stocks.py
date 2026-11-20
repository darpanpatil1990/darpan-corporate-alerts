"""Top 10/20 AMC accumulation x Monthly/Quarterly demand: research-only dashboard segment.
Runs on the completed existing MF ranker output. Uses already dated provider data.
No transaction price is inferred from monthly quantity change.
"""
import argparse, csv, html, json, math, re
from collections import defaultdict
from datetime import date
from pathlib import Path
from mf_fund_ranker import read_state

TIMEFRAMES = {"Monthly", "Quarterly"}
BUY_TYPES = {"NEW_POSITION", "INCREASED"}

def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) and x > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None

def classify(cmp, zones, close_confirmed):
    """Only recognized, nonbroken M/Q zones. Never label above-zone as a rally without a touch and close."""
    matches = []
    for z in zones:
        if z.get("tf") not in TIMEFRAMES or z.get("broken") is True:
            continue
        d, p = number(z.get("d")), number(z.get("p"))
        if d is None or p is None or d >= p:
            continue
        status, distance = None, None
        if d <= cmp <= p:
            status, distance = "IN_ZONE", 0
        elif p < cmp <= p * 1.05:
            status, distance = "APPROACH_0_5", round(100 * (cmp - p) / p, 2)
        elif cmp >= p * 1.05 and z.get("latestTouch") and close_confirmed:
            status, distance = "POST_TOUCH_CLOSE_5_PLUS", round(100 * (cmp - p) / p, 2)
        if status:
            matches.append({"tf": z["tf"], "d": d, "p": p,
                "date": z.get("date"), "status": status, "distance_pct": distance,
                "achievement": z.get("achievement", 0),
                "touch": z.get("latestTouch") or z.get("firstTouch")})
    priority = {"IN_ZONE": 0, "APPROACH_0_5": 1, "POST_TOUCH_CLOSE_5_PLUS": 2}
    matches.sort(key=lambda z: (priority[z["status"]], z["distance_pct"], z["tf"]))
    return matches

def eligible_deals(record, quote_asof):
    out = []
    for e in record.get("evidence", []):
        kind = str(e.get("kind", ""))
        buyer = str(e.get("buyer", ""))
        if not (kind.startswith("Bulk disclosed acquisition") or kind.startswith("Block disclosed acquisition")):
            continue
        if not re.search(r"\b(?:mutual fund|asset management|AMC|MF)\b", buyer, re.I):
            continue
        dt = str(e.get("end", ""))
        if not dt or dt > quote_asof[:10] or not e.get("source"):
            continue
        out.append({"buyer": buyer, "kind": kind, "date": dt, "source": e["source"]})
    return sorted(out, key=lambda r: r["date"], reverse=True)[:8]

def build_top_stocks(report, state):
    stocks = state.get("records", {})
    summary = defaultdict(lambda: {"funds": set(), "buying_funds": set(), "amcs": set(),
                                   "buying_amcs": set(), "buyers": []})
    for fund in report.get("funds", []):
        if fund.get("rank") is None:
            continue
        fid = (str(fund.get("amc_id", "")), str(fund.get("fund", "")).casefold())
        by_symbol = {x.get("stock"): x for x in fund.get("month_change_details", [])
                     if x.get("kind") in BUY_TYPES}
        for p in fund.get("stocks", []):
            symbol = p.get("stock")
            if not symbol or not p.get("verified"):
                continue
            s = summary[symbol]
            s["funds"].add(fid)
            s["amcs"].add(fid[0])
            movement = by_symbol.get(symbol)
            if movement and movement.get("asof") == p.get("period"):
                s["buying_funds"].add(fid)
                s["buying_amcs"].add(fid[0])
                s["buyers"].append({"fund": fund.get("fund"), "amc_id": fid[0],
                                    "type": movement["kind"], "period": movement["asof"]})
    result = []
    for symbol, s in summary.items():
        record = stocks.get(symbol) or {}
        cmp = number(record.get("cmp"))
        if not cmp:
            continue
        quality = record.get("quality") or {}
        quote = quality.get("quote") or {}
        asof = str(quote.get("asof") or record.get("asof") or "")[:10]
        close_confirmed = "close" in str(quote.get("kind", "")).lower()
        if not asof:
            continue
        # Reject quotes older than 10 calendar days and records not in the same dated universe.
        try:
            age = (date.fromisoformat(report["generated_at"][:10]) - date.fromisoformat(asof)).days
        except (KeyError, ValueError):
            continue
        if age < 0 or age > 10 or not quality.get("current_alerts_eligible", True):
            continue
        zones = classify(cmp, record.get("zones", []), close_confirmed)
        if not zones:
            continue
        statuses = {z["status"] for z in zones}
        status = next(x for x in ("IN_ZONE", "APPROACH_0_5", "POST_TOUCH_CLOSE_5_PLUS") if x in statuses)
        tfs = sorted({z["tf"] for z in zones})
        achievements = sorted({z["tf"] for z in zones if number(z.get("achievement"))})
        deals = eligible_deals(record, asof)
        result.append({
            "symbol": symbol, "cmp_inr": round(cmp, 2), "quote_date": asof,
            "quote_kind": quote.get("kind", "Unverified quote kind"), "status": status,
            "timeframes": tfs, "zones": zones, "achievement_tfs": achievements,
            "holding_funds": len(s["funds"]), "holding_amcs": len(s["amcs"]),
            "buying_funds": len(s["buying_funds"]), "buying_amcs": len(s["buying_amcs"]),
            "recent_monthly_changes": sorted(s["buyers"], key=lambda x: (x["fund"] or ""))[:30],
            "disclosed_bulk_block_mf_buys": deals,
            "data_status": "MONTHLY_QUANTITY_CONFLUENCE_NOT_TRADE_PRICE",
        })
    order = {"IN_ZONE": 0, "APPROACH_0_5": 1, "POST_TOUCH_CLOSE_5_PLUS": 2}
    result.sort(key=lambda x: (
        -int(bool(x["disclosed_bulk_block_mf_buys"]) and x["status"] == "IN_ZONE"),
        -int(x["buying_amcs"] >= 2 and x["status"] == "IN_ZONE"),
        -int(x["buying_amcs"] > 0 and x["status"] == "IN_ZONE"),
        order[x["status"]], -x["buying_amcs"], -x["buying_funds"],
        -int(len(x["timeframes"]) == 2), -len(x["achievement_tfs"]),
        -x["holding_amcs"], x["symbol"]))
    for i, s in enumerate(result, 1):
        s["rank"] = i
    return result

def segment(report):
    payload = json.dumps(report.get("top_stocks", []), ensure_ascii=False).replace("<", "\\u003c")
    built = html.escape(str(report.get("generated_at", "Unknown")), quote=True)
    return r"""
<style>
#top-stock-segment{margin:20px 0 24px;padding:18px;background:#fff;border:1px solid #dae1ee;border-radius:14px;color:#14243d}
#top-stock-segment h3{margin:0 0 7px;font-size:21px}
#top-stock-segment .note{color:#53647a;font-size:13px}
#top-stock-segment .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0}
#top-stock-segment button,#top-stock-segment select,#top-stock-segment input{padding:9px;border:1px solid #c7d3e5;border-radius:9px;background:#f8faff;color:#14243d}
#top-stock-segment button{cursor:pointer}
#top-stock-segment button.active{background:#174b92;color:#fff}
#top-stock-segment .scroll{overflow:auto}
#top-stock-segment table{min-width:900px}
#top-stock-segment td{vertical-align:top}
#top-stock-segment .tag{display:inline-block;border-radius:5px;padding:3px 5px;background:#eaf0fb;margin:2px;font-size:12px}
</style>
<section id="top-stock-segment" aria-label="AMC demand-zone top stocks">
<h3>Top 10 / Top 20 — AMC Buying × Monthly / Quarterly Demand Zones</h3>
<div class="note">As of __DATE__ · refreshed on each successful ranking run. Monthly disclosed share-quantity change is NOT proof of the purchase date or execution price. Bulk/Block evidence is separate. One stock counts once even if in both timeframes. Rankings provisional.</div>
<div class="toolbar">
<button id="ts10" type="button" class="active">Top 10</button>
<button id="ts20" type="button">Top 20</button>
<select id="tsstatus" aria-label="Zone status"><option value="">All zone statuses</option><option value="IN_ZONE">Inside zone</option><option value="APPROACH_0_5">0–5% above zone</option><option value="POST_TOUCH_CLOSE_5_PLUS">Post-touch confirmed close +5%</option></select>
<select id="tstf" aria-label="Timeframe"><option value="">Monthly + Quarterly</option><option value="Monthly">Monthly</option><option value="Quarterly">Quarterly</option><option value="BOTH">Both M/Q</option></select>
<label><input type="checkbox" id="tsbuy"> Monthly fresh / increased only</label>
<input id="tsquery" placeholder="Search stock" aria-label="Stock search">
<button id="tscsv" type="button">Export CSV</button>
</div>
<p class="note" id="tscount"></p>
<div class="scroll"><table><thead><tr><th>Rank</th><th>Stock / CMP</th><th>Zone</th><th>Demand range (₹)</th><th>AMC new/increased</th><th>Funds new/increased</th><th>Holding AMCs</th><th>M/Q + Achievement</th><th>Reported MF deals</th></tr></thead><tbody id="tsrows"></tbody></table></div>
<p class="note">Buy signal = comparison of two monthly AMC holdings disclosures; same-zone execution cannot be established without a dated verified deal. This screen is research, not a trade recommendation. Backtested performance is unverified.</p>
</section>
<script>
(function() {
const stocks=__STOCKS__;
const $=id=>document.getElementById(id),esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let limit=10;
const statusName={IN_ZONE:"In zone",APPROACH_0_5:"0–5% above",POST_TOUCH_CLOSE_5_PLUS:"Post-touch +5% close"};
function selected(){
return stocks.filter(s=>(!$("tsstatus").value||s.status===$("tsstatus").value)&&
(!$("tstf").value||($("tstf").value==="BOTH"?s.timeframes.length===2:s.timeframes.includes($("tstf").value)))&&
(!$("tsbuy").checked||s.buying_funds>0)&&
(!$("tsquery").value||s.symbol.toLowerCase().includes($("tsquery").value.toLowerCase()))).slice(0,limit);
}
function draw(){
$("ts10").className=limit===10?"active":"";$("ts20").className=limit===20?"active":"";
const rows=selected();
$("tscount").textContent=rows.length+" shown / "+stocks.length+" eligible covered stocks. Positions and rankings depend on disclosure availability.";
$("tsrows").innerHTML=rows.map(s=>"<tr><td>"+s.rank+"</td><td><b>"+esc(s.symbol)+"</b><div>₹"+esc(s.cmp_inr)+" · "+esc(s.quote_date)+"</div></td><td>"+esc(statusName[s.status])+"</td><td>"+
s.zones.map(z=>'<div><span class="tag">'+esc(z.tf)+"</span> ₹"+esc(z.d)+"–₹"+esc(z.p)+"</div>").join("")+"</td><td>"+s.buying_amcs+"</td><td>"+s.buying_funds+"</td><td>"+s.holding_amcs+"</td><td>"+
s.timeframes.map(t=>'<span class="tag">'+esc(t)+(s.achievement_tfs.includes(t)?" ✓ Achievement":"")+"</span>").join("")+
"</td><td>"+s.disclosed_bulk_block_mf_buys.length+
"<details><summary>Evidence / MF monthly changes</summary>"+
s.disclosed_bulk_block_mf_buys.map(e=>'<div>'+esc(e.date)+" "+esc(e.buyer)+"</div>").join("")+
s.recent_monthly_changes.map(e=>'<div>'+esc(e.period)+" "+esc(e.fund)+" "+esc(e.type)+"</div>").join("")+"</details></td></tr>").join("")||
'<tr><td colspan="9">No verified matches for these filters; no results are invented.</td></tr>';
}
$("ts10").onclick=()=>{limit=10;draw()};$("ts20").onclick=()=>{limit=20;draw()};
["tsstatus","tstf","tsbuy","tsquery"].forEach(id=>$(id).addEventListener("input",draw));
$("tscsv").onclick=()=>{const keys=["rank","symbol","cmp_inr","quote_date","status","buying_amcs","buying_funds","holding_amcs"];
const csv=[keys.join(","),...selected().map(s=>keys.map(k=>JSON.stringify(s[k]??"")).join(","))].join("\r\n");
const a=document.createElement("a");const u=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));a.href=u;a.download="mf-top-stocks.csv";a.click();URL.revokeObjectURL(u)};
draw();
})();
</script>
""".replace("__STOCKS__", payload).replace("__DATE__", built)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranking", default="mf-ranking-output/ranking.json")
    parser.add_argument("--state", default="institutional-confluence-state.json")
    parser.add_argument("--output", default="mf-ranking-output")
    args = parser.parse_args()
    report = json.loads(Path(args.ranking).read_text(encoding="utf-8"))
    state = read_state(args.state)
    items = build_top_stocks(report, state)
    report["top_stocks"] = items
    report["top_stocks_method"] = "Monthly/Quarterly unique-stock confluence; month-to-month quantity change only, no confirmed execution price"
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ranking.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    src = out / "dashboard.html"
    dashboard = src.read_text(encoding="utf-8")
    if 'id="top-stock-segment"' in dashboard:
        raise SystemExit("Top stocks segment already included")
    marker = '<p id="counts"></p>'
    if marker not in dashboard:
        raise SystemExit("Existing dashboard format changed; refusing unverified insertion")
    dashboard = dashboard.replace(marker, marker + segment(report), 1)
    src.write_text(dashboard, encoding="utf-8")
    cols = ["rank","symbol","cmp_inr","quote_date","status","buying_amcs","buying_funds","holding_amcs"]
    with (out / "top-stocks.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)
    print("TOP STOCKS", len(items), "PUBLISHED IN SAME HTML SEGMENT; TEST/PERFORMANCE BACKTEST NOT CLAIMED")

if __name__ == "__main__":
    main()
