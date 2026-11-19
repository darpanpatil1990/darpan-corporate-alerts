"""Separate ScanX equity fund demand ranking. Research only; does not modify alerts.

Denominator is VERIFIED eligible Nifty-500 stock holdings, not all fund holdings.
Do not describe partial source coverage as a complete fund portfolio.
"""
import argparse,base64,csv,gzip,json,lzma,os,re,time,urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,date,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

IST=ZoneInfo('Asia/Kolkata')
API='https://ow-static-scanx.dhan.co/staticscanx/mfpastholdingsbyisin'
AMCS='https://ow-static-scanx.dhan.co/staticscanx/amclist_v2'
FIELDS=['amcid','sch','planid','isin','shares','assetPerc','onemonthSharesCh','onemonthCh','threemonthCh','date','seo']
TF={'Monthly','Quarterly','Half Yearly'}
EXCLUDE=[
 (r'\belss\b|tax[\s-]*(?:saving|saver|shield)|80c','ELSS'),
 (r'\barbitrage\b','Arbitrage'),
 (r'\b(liquid|overnight|debt|money market|corporate bond|dynamic bond|gilt|g[\s-]*sec|government securities|treasury|duration|credit risk|income fund|target maturity|floater|floating rate)\b','Debt/cash'),
 (r'\b(hybrid|balanced|equity savings|multi[\s-]*asset|asset allocation|conservative|retirement|children|fund of funds|fof|gold|silver|commodity)\b','Hybrid/other asset'),
]
INCLUDE=[
 (r'large[\s&-]*(?:and[\s-]*)?mid[\s-]*cap','Large & Mid Cap'),
 (r'small[\s-]*cap','Small Cap'),(r'mid[\s-]*cap','Mid Cap'),
 (r'large[\s-]*cap|blue[\s-]*chip|top[\s-]*100','Large Cap'),
 (r'flexi[\s-]*cap','Flexi Cap'),(r'multi[\s-]*cap','Multi Cap'),
 (r'\bfocused\b','Focused'),(r'\bvalue\b|\bcontra\b','Value / Contra'),
 (r'dividend[\s-]*yield','Dividend Yield'),
 (r'sector|thematic|manufactur|infrastruct|technolog|health|pharma|banking|financial service|consumption|energy|defen[cs]e|psu|business cycle|digital|innovation|transport|automob|realty|momentum|quality|low volatil|alpha|opportunities','Sectoral / Thematic'),
 (r'\b(nifty|sensex|index|etf|equal[\s-]*weight|factor)\b','Equity Index / ETF'),
 (r'\b(equity|growth|active|quant)\b','Other Equity'),
]

def category(name,hint=''):
    t=(str(name)+' '+str(hint)).casefold()
    for pattern,label in EXCLUDE:
        if re.search(pattern,t,re.I):return None,label
    for pattern,label in INCLUDE:
        if re.search(pattern,t,re.I):return label,None
    if re.search(r'equity\s+(?:fund|scheme)',str(hint),re.I):return 'Other Equity',None
    return None,'Unclassified'

def canonical(name):
    s=re.sub(r'\s+',' ',str(name)).strip()
    s=re.sub(r'\s*[-–(]?\s*(?:direct|regular)\s*(?:plan)?\b','',s,flags=re.I)
    s=re.sub(r'\s*[-–(]?\s*(?:growth|idcw|dividend payout|dividend reinvestment)\b.*$','',s,flags=re.I)
    return s.strip(' -(') or str(name)

def last_day(text):
    if not re.fullmatch(r'\d{4}-\d{2}',text):raise ValueError('Unknown MF reporting month')
    d=date.fromisoformat(text+'-01').replace(day=28)+timedelta(days=4)
    return d.replace(day=1)-timedelta(days=1)

def decimal(value):
    try:q=Decimal(str(value).replace(',','').replace('%',''))
    except (ValueError,InvalidOperation):raise ValueError('Invalid MF quantity')
    if not q.is_finite():raise ValueError('Non-finite MF quantity')
    return q

def parse_page(data,isin,today):
    """Read month-end quantities: increases are not verified exchange purchases."""
    if data.get('code')!=0 or data.get('headers')!=FIELDS:raise ValueError('ScanX holdings schema changed')
    result=[];identities=set()
    for vals in data.get('data',[]):
        if not isinstance(vals,list) or len(vals)!=len(FIELDS):raise ValueError('Invalid holdings row')
        r=dict(zip(FIELDS,vals));key=(str(r['amcid']),str(r['planid']),str(r['isin']))
        if key in identities:raise ValueError('Duplicate scheme')
        identities.add(key)
        dates=str(r['date']).split('|');quantities=str(r['shares']).split('|')
        weights=str(r['assetPerc']).split('|')
        if len(dates)!=len(quantities) or not dates:raise ValueError('Undated holding')
        if len(weights)!=len(dates):raise ValueError('Allocation history does not match quantity dates')
        monthly={}
        for stamp,value,weight in zip(dates,quantities,weights):
            q=decimal(value)
            if q<0:raise ValueError('Negative holding')
            w=decimal(weight) if str(weight).strip() else None
            issue='WEIGHT_OUT_OF_RANGE' if w is not None and not 0<=w<=100 else None
            if issue:w=None  # Quarantine the weight, NOT the independently verified share quantity.
            if stamp in monthly and monthly[stamp]!=(q,w,issue):raise ValueError('Conflicting monthly holding')
            monthly[stamp]=(q,w,issue)
        periods=sorted(monthly,key=last_day)
        current=periods[-1];period=last_day(current);qty,weight,weight_issue=monthly[current]
        if not 0<=(today-period).days<=62:continue
        previous=periods[-2] if len(periods)>1 else None
        prior_qty=monthly[previous][0] if previous else None
        if qty==0 and (prior_qty is None or prior_qty==0):continue
        delta=(qty-prior_qty) if prior_qty is not None else None
        trend=('NO_HISTORY' if delta is None else 'EXITED' if qty==0 else
               'NEW_POSITION' if prior_qty==0 else 'INCREASED' if delta>0 else
               'REDUCED' if delta<0 else 'UNCHANGED')
        result.append({'amc':str(r['amcid']),'scheme_id':str(r['planid']),
            'scheme':canonical(r['sch']),'qty':str(qty),
            'previous_qty':str(prior_qty) if prior_qty is not None else None,
            'change_qty':str(delta) if delta is not None else None,
            'change_type':trend,'previous_period':last_day(previous).isoformat() if previous else None,
            'weight_pct':float(weight) if weight is not None else None,
            'weight_issue':weight_issue,
            'period':period.isoformat(),'stock_isin':isin,
            'caveat':'Monthly disclosed quantity difference; not proof of a transaction or NFO status.'})
    return result,identities

def get_json(endpoint,data=None):
    headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/html,*/*'}
    payload=None if data is None else json.dumps(data).encode()
    if payload is not None:headers.update({'Content-Type':'application/json','Origin':'https://scanx.trade'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(endpoint,data=payload,headers=headers),timeout=25) as r:
                return json.load(r)
        except Exception:
            if attempt==2:raise
            time.sleep(2**(attempt+1))

def holdings(isin,today,cache):
    target=cache/(isin+'.json.gz')
    if target.exists() and 0<=time.time()-target.stat().st_mtime<21600:
        try:return json.loads(gzip.decompress(target.read_bytes()))
        except (OSError,ValueError):pass
    rows=[];keys=set();total=None;page=0
    while True:
        page+=1
        if page>100:raise ValueError('MF pagination >100 pages')
        data=get_json(API,{'data':{'isin':isin,'page':page,'pageSize':1000}})
        if data.get('code')!=0:raise ValueError('ScanX provider response not successful')
        if not data.get('data') and int(data.get('totalcount',0))==0:break
        batch,ids=parse_page(data,isin,today)
        if keys&ids:raise ValueError('Repeated scheme across pages')
        keys|=ids;rows+=batch
        n=int(data['totalcount']);pages=int(data['totalpages'])
        if total is not None and n!=total:raise ValueError('Changing paginated source')
        total=n
        if page>=pages:break
    if total is not None and len(keys)!=total:raise ValueError('Incomplete scheme pages')
    cache.mkdir(parents=True,exist_ok=True)
    target.write_bytes(gzip.compress(json.dumps(rows,separators=(',',':')).encode()))
    return rows


def catalogue_changes(catalogue, cache_dir, today):
    """Detect newly listed provider entries, NOT verified NFO launch dates.

    The first run establishes a baseline. Any absence/invalid provider response must
    not erase the prior catalogue. Catalogue keys never contribute to fund scores.
    """
    if catalogue.get('code')!=0 or not isinstance(catalogue.get('data'),list):
        raise ValueError('AMC catalogue unavailable')
    entries={};amc_count=0;unknown=0
    for amc in catalogue['data']:
        if not isinstance(amc,dict):raise ValueError('Invalid AMC catalogue row')
        amcid=str(amc.get('amcid') or amc.get('id') or '').strip()
        if not amcid:unknown+=1;continue
        amc_count+=1
        schemes=amc.get('scheme',[])
        if not isinstance(schemes,list):raise ValueError('Invalid scheme catalogue')
        for sch in schemes:
            if isinstance(sch,str):name=canonical(sch);sid=name.casefold()
            elif isinstance(sch,dict):
                name=canonical(sch.get('sch') or sch.get('name') or
                               sch.get('schemeName') or '')
                sid=str(sch.get('planid') or sch.get('schemeid') or
                        sch.get('id') or name.casefold())
            else:
                unknown+=1;continue
            if not name or not sid:unknown+=1;continue
            key=amcid+':'+sid
            if key in entries and entries[key]['fund']!=name:
                raise ValueError('Conflicting catalogue entries')
            entries[key]={'amc_id':amcid,'fund':name,'type':category(name)[0]}
    if not entries:raise ValueError('AMC catalogue contains no identifiable schemes')
    cache_dir.mkdir(parents=True,exist_ok=True)
    file=cache_dir/'catalogue.json'
    previous=None
    if file.exists():
        old=json.loads(file.read_text())
        if isinstance(old,dict) and isinstance(old.get('entries'),dict):
            previous=old['entries']
    new=[entries[key] for key in sorted(set(entries)-set(previous or {}))] if previous is not None else []
    status={'source':AMCS,'checked_at':today.isoformat(),'amcs_identified':amc_count,
        'schemes_identified':len(entries),'unknown_rows':unknown,
        'baseline_created':previous is None,
        'new_provider_listings':new,
        'new_listing_caveat':'New to the ScanX catalogue since last verified snapshot. Not confirmed NFOs; launch status and dates require official AMC/AMFI verification.',
        'catalogue_completeness_confirmed':unknown==0}
    # Atomic snapshot update after successful parsing.
    tmp=file.with_suffix('.tmp')
    tmp.write_text(json.dumps({'checked_at':today.isoformat(),'entries':entries},separators=(',',':')))
    tmp.replace(file)
    return status

def read_state(file):
    state=json.loads(Path(file).read_text())
    if state.get('encoding') in ('xz-base64','gzip-base64'):
        b=base64.b64decode(state['data'],validate=True)
        plain=lzma.decompress(b) if state['encoding']=='xz-base64' else gzip.decompress(b)
        if len(plain)>30000000:raise ValueError('Oversized state')
        state=json.loads(plain)
    if not isinstance(state.get('records'),dict):raise ValueError('No existing zone records')
    return state

def zone_info(stock,today):
    if not stock:return {'verified':False,'asof':None,'zones':[]}
    try:asof=date.fromisoformat(str(stock.get('asof',''))[:10])
    except ValueError:return {'verified':False,'asof':None,'zones':[]}
    fresh=0<=(today-asof).days<=10 and stock.get('quality',{}).get('current_alerts_eligible',True)
    z=[{'tf':item['tf'],'date':item['date'],'p':item['p'],'d':item['d'],
        'achievement':item.get('achievement',0),'first_return':bool(item.get('firstReturn'))}
       for item in stock.get('zones',[])
       if item.get('tf') in TF and item.get('broken') is not True
          and all(k in item for k in ('tf','date','p','d'))]
    return {'verified':bool(fresh),'asof':asof.isoformat(),
            'zones':z if fresh else []}

def ranking(source,stocks,today):
    """Count distinct verified zone stocks first; never rank by past NAV returns."""
    grouped={};excluded=Counter()
    for symbol,items in source.items():
        for item in items:
            group,reason=category(item['scheme'])
            if group is None:
                excluded[reason]+=1;continue
            key=(item['amc'],item['scheme'].casefold())
            f=grouped.setdefault(key,{'fund':item['scheme'],'amc_id':item['amc'],
                                      'category':group,'positions':{},'activity':{}})
            if item.get('change_type') not in (None,'NO_HISTORY','UNCHANGED'):
                f['activity'][symbol]={'stock':symbol,'kind':item['change_type'],
                    'previous_qty':item.get('previous_qty'),'current_qty':item['qty'],
                    'change_qty':item.get('change_qty'),'asof':item['period'],
                    'source':'ScanX monthly reported quantities; not a confirmed trade'}
            if decimal(item.get('qty',1))<=0:continue   # exits are reported, never ranked as holdings
            pos=f['positions'].get(symbol)
            if pos and pos['period']>=item['period']:continue
            f['positions'][symbol]={**zone_info(stocks.get(symbol),today),
                'stock':symbol,'weight_pct':item['weight_pct'],
                'weight_issue':item.get('weight_issue'),'period':item['period']}
    out=[]
    for f in grouped.values():
        positions=list(f['positions'].values())
        if not positions:continue   # full exits cannot masquerade as an active fund holding
        verified=[p for p in positions if p['verified']]
        hit=[p for p in verified if p['zones']]
        ach=[p for p in hit if any(isinstance(z['achievement'],(float,int)) and z['achievement']>=1 for z in p['zones'])]
        by_tf={x:sum(any(z['tf']==x for z in p['zones']) for p in hit) for x in TF}
        weighted=[p['weight_pct'] for p in hit if p['weight_pct'] is not None]
        changes=list(f['activity'].values())
        change_counts=dict(Counter(row['kind'] for row in changes))
        out.append({'fund':f['fund'],'amc_id':f['amc_id'],'category':f['category'],
            'holdings_in_scanned_universe':len(positions),'verified_holdings':len(verified),
            'zone_stocks':len(hit),'achievement_stocks':len(ach),
            'zone_pct_verified':round(100*len(hit)/len(verified),2) if verified else None,
            'zone_portfolio_weight_pct':round(sum(weighted),4) if len(weighted)==len(hit) else None,
            'zone_weight_known_stocks':len(weighted),
            'invalid_weight_positions':sum(bool(p.get('weight_issue')) for p in positions),
            'monthly':by_tf['Monthly'],'quarterly':by_tf['Quarterly'],'half_yearly':by_tf['Half Yearly'],
            'latest_holdings_month':max(p['period'] for p in positions),
            'month_changes':change_counts,'month_change_details':sorted(changes,key=lambda x:x['stock']),
            'ranking_scope':'PROVISIONAL: verified eligible Nifty 500 positions only, full portfolio not verified',
            'stocks':sorted(hit,key=lambda x:x['stock'])})
    out.sort(key=lambda x:(-x['zone_stocks'],-(x['zone_portfolio_weight_pct'] or 0),
             -x['achievement_stocks'],-(x['zone_pct_verified'] or 0),x['fund'].casefold()))
    ordinal=0
    for row in out:
        if row['verified_holdings']==0:
            row['rank']=None
            row['rank_status']='WITHHELD: no fresh verified stock-zone observations'
        else:
            ordinal+=1
            row['rank']=ordinal
            row['rank_status']='PROVISIONAL: missing full-fund universe coverage'
    return out,dict(excluded)

def html_dashboard(report):
    payload=json.dumps(report,ensure_ascii=False).replace('<','\\u003c')
    return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MF Demand Zone Fund Ranking</title><style>body{font:15px system-ui;background:#f6f8fc;color:#15223b;margin:24px}
table{width:100%;border-collapse:collapse;background:white}td,th{padding:10px;border-bottom:1px solid #e2e8f0;text-align:left}
th{background:#eaf0f9}select,input{padding:10px;margin:7px;border:1px solid #cbd5e1;border-radius:7px}
td:nth-child(n+5){text-align:right}small{color:#506179}details{margin-top:7px}summary{cursor:pointer;color:#075cad}
</style></head><body><h2>Mutual Fund · HTF Demand Ranking</h2><p id="counts"></p>
<p><b>Research scope:</b> Nifty 500 stocks with market cap > ₹500 Cr and verified zone data.
Fund-holdings counts only reflect this covered stock universe, NOT entire portfolio.
Excludes ELSS, arbitrage, debt, liquid, hybrid and non-equity schemes. No guaranteed rally.</p>
<input id="query" placeholder="Search fund" /><select id="top"><option value="0">All</option><option value="10">Top 10</option><option value="20">Top 20</option><option value="50">Top 50</option></select><select id="category"><option value="">All categories</option></select>
<select id="amc"><option value="">All AMCs</option></select>
<div style="overflow:auto"><table><thead><tr><th>#</th><th>Fund / zones</th><th>AMC ID</th><th>Category</th>
<th>Demand</th><th>Verified / Covered</th><th>Demand %</th><th>Weight %</th><th>Achievement</th><th>M/Q/HY</th><th>Month changes</th><th>Status</th></tr></thead>
<tbody id="results"></tbody></table></div><p><small id="coverage"></small></p>
<script>
const data=__DATA__,funds=data.funds;
const q=document.getElementById('query'),top=document.getElementById('top'),cat=document.getElementById('category'),
amc=document.getElementById('amc'),body=document.getElementById('results');
const esc=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
for(const v of [...new Set(funds.map(f=>f.category))].sort())cat.add(new Option(v,v));
for(const v of [...new Set(funds.map(f=>f.amc_id))].sort())amc.add(new Option(v,v));
document.getElementById('counts').textContent=data.generated_at+' | '+funds.filter(f=>f.rank!==null).length+' provisional rankings; '+funds.filter(f=>f.rank===null).length+' withheld';
document.getElementById('coverage').textContent='Stocks fetched: '+data.coverage.fetched+
' / '+data.coverage.candidates+'; partial data: '+data.coverage.partial+
'. Fund holdings outside selected stock universe remain unknown.';
function draw(){
const rows=funds.filter(f=>(!top.value||+top.value===0||(f.rank!==null&&f.rank<=+top.value))&&(!cat.value||cat.value===f.category)&&(!amc.value||amc.value===f.amc_id)&&
(!q.value||f.fund.toLowerCase().includes(q.value.toLowerCase())));
body.innerHTML=rows.map(f=>'<tr><td>'+f.rank+'</td><td><b>'+esc(f.fund)+'</b><details><summary>Show stock zones</summary>'+
f.stocks.map(p=>'<div>'+esc(p.stock)+' ('+esc(p.weight_pct??'unknown')+'% weight) · '+p.zones.map(z=>esc(z.tf)+' '+z.d+'–'+z.p+' (achievement '+z.achievement+')').join(', ')+'</div>').join('')+
'</details></td><td>'+esc(f.amc_id)+'</td><td>'+esc(f.category)+'</td><td>'+f.zone_stocks+
'</td><td>'+f.verified_holdings+' / '+f.holdings_in_scanned_universe+'</td><td>'+f.zone_pct_verified+
'</td><td>'+esc(f.zone_portfolio_weight_pct??'unknown')+'</td><td>'+f.achievement_stocks+'</td><td>'+f.monthly+'/'+f.quarterly+'/'+f.half_yearly+'</td><td><details><summary>'+esc(Object.entries(f.month_changes||{}).map(([k,v])=>k+':'+v).join(', ')||'None')+'</summary>'+((f.month_change_details||[]).map(m=>'<div>'+esc(m.stock)+' · '+esc(m.kind)+' · '+esc(m.previous_qty)+' → '+esc(m.current_qty)+'</div>').join(''))+'</details></td><td>'+esc(f.rank_status)+'</td></tr>').join('');
}
q.oninput=draw;top.onchange=draw;cat.onchange=draw;amc.onchange=draw;draw();
</script></body></html>""".replace('__DATA__',payload)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--state',default='institutional-confluence-state.json')
    p.add_argument('--output',default='mf-ranking-output')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--limit',type=int,default=0,help='Development-only partial run')
    a=p.parse_args();today=datetime.now(IST).date()
    state=read_state(a.state)
    snapshot=state.get('universe_snapshot',{})
    members=snapshot.get('members',{})
    if not isinstance(members,dict) or len(members)<450:
        raise SystemExit('Verified official Nifty 500 identities missing; not inventing holdings')
    stocks={}
    for symbol,isin in members.items():
        rec=state['records'].get(symbol)
        if not rec or rec.get('quality',{}).get('stock_isin')!=isin:continue
        try:cap=Decimal(str(rec.get('market_cap_cr',0)))
        except InvalidOperation:continue
        if cap>500:stocks[symbol]=rec
    candidates=list(stocks)
    if a.limit:candidates=candidates[:a.limit]
    found={};errors={}
    with ThreadPoolExecutor(max_workers=min(6,max(1,a.workers))) as pool:
        tasks={pool.submit(holdings,members[s],today,Path('.scanner-cache/mf-ranker')):s for s in candidates}
        for future in as_completed(tasks):
            symbol=tasks[future]
            try:
                found[symbol]=future.result()
                print('SCANX',symbol,'fund rows',len(found[symbol]),flush=True)
            except Exception as e:
                errors[symbol]=type(e).__name__+': '+str(e)[:150]
                print('UNAVAILABLE',symbol,errors[symbol],flush=True)
    funds,excluded=ranking(found,stocks,today)
    weight_issues=[{'stock':symbol,'fund':item['scheme'],'reason':item['weight_issue']}
                   for symbol,rows in found.items() for item in rows if item.get('weight_issue')]
    coverage={'universe':'Nifty 500 with market cap > INR 500 Cr',
              'official_membership_asof':snapshot.get('verified_at'),
              'candidates':len(candidates),'fetched':len(found),'errors':errors,
              'partial':bool(errors or len(stocks)<len(members) or a.limit),
              'scope':'Holdings outside this tracked stock universe are unknown, not zero.',
              'mf_source':API,'amc_source':AMCS,
              'invalid_weight_rows':len(weight_issues),
              'weight_issues_preview':weight_issues[:50],
              'weight_coverage_caveat':'Out-of-range provider allocation is excluded from weight scores; verified quantity and zone count retained.'}
    try:
        cat=get_json(AMCS)
        if isinstance(cat,dict):
            samples=(cat.get('data') or [])[:2] if isinstance(cat.get('data'),list) else []
            print('AMC CATALOGUE SCHEMA',
                json.dumps({'root_keys':list(cat),'data_type':type(cat.get('data')).__name__,
                    'sample_record_types':[type(x).__name__ for x in samples],
                    'sample_keys':[list(x) if isinstance(x,dict) else [] for x in samples],
                    'sample_records':[str(x)[:250] for x in samples]},ensure_ascii=False),flush=True)
        catalogue=catalogue_changes(cat,Path('.scanner-cache/mf-ranker'),today)
        coverage['provider_amc_count']=catalogue['amcs_identified']
        coverage['provider_scheme_count']=catalogue['schemes_identified']
        coverage['catalogue']=catalogue
        if not catalogue['catalogue_completeness_confirmed']:coverage['partial']=True
    except Exception as e:
        coverage['amc_error']=type(e).__name__+': '+str(e)[:150]
        coverage['partial']=True
    report={'generated_at':datetime.now(IST).isoformat(),
            'ranking':'RESEARCH ONLY: distinct verified demand-zone stocks, then confirmed weightage, achievement; never trailing fund returns or age',
            'coverage':coverage,'excluded_rows':excluded,'funds':funds}
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    (out/'ranking.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (out/'dashboard.html').write_text(html_dashboard(report),encoding='utf-8')
    cols=['rank','fund','amc_id','category','zone_stocks','verified_holdings',
          'holdings_in_scanned_universe','zone_pct_verified','zone_portfolio_weight_pct','achievement_stocks',
          'monthly','quarterly','half_yearly','latest_holdings_month','rank_status']
    with (out/'ranking.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore')
        writer.writeheader();writer.writerows(funds)
    print('FUNDS',len(funds),'SOURCE',len(found),'/',len(candidates),
          'PARTIAL',coverage['partial'],flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## MF Demand Zone Fund Ranking\n\nFunds ranked: '+str(len(funds))+
                '; stocks checked: '+str(len(found))+'/'+str(len(candidates))+
                '; partial coverage: '+str(coverage['partial'])+
                '. Counts reflect covered Nifty-500 stock universe, not entire fund portfolios.\n')
    if coverage['partial']:
        raise SystemExit('Rankings saved with partial data warning; source/stock gaps above.')

if __name__=='__main__':main()
