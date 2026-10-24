"""Full Nifty universe, dated multi-AMC evidence, public delayed quotes and safe delivery.
External source omissions remain explicit; attempted coverage is never called verified data.
"""
import base64, csv, gzip, hashlib, io, json, math, os, re, subprocess, sys, threading, time
import urllib.error, urllib.parse as url, urllib.request as web
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from bs4 import BeautifulSoup
from institutional_confluence import (Store, request, UNIVERSE_URL, universe_from_csv, IST,
    day, month_end, login, trade_rows, overlaps, verify_private_destination, BASE, TRADE_PATHS)

MF_API='https://ow-static-scanx.dhan.co/staticscanx/mfpastholdingsbyisin'
CAP_API='https://ow-scanx-analytics.dhan.co/customscan/fetchdt'
CAP_LOOKUP={}
# Yahoo search identifies this NSE REIT series explicitly; bind the alias to its stock ISIN.
YAHOO_QUOTE_ALIASES={'BAGMANE':('INE2OVN25015','BAGMANE-RR')}
AMC_API='https://ow-static-scanx.dhan.co/staticscanx/amclist_v2'
ROOT=Path(__file__).resolve().parent
CACHE=ROOT/'.scanner-cache'
DISCLOSURE_GATE=threading.BoundedSemaphore(1)
LOCKS={k:threading.BoundedSemaphore(3) for k in ('scanx','yahoo','screener')}

def read_bytes(address,payload=None):
    host=url.urlparse(address).hostname or ''
    group='scanx' if 'dhan.co' in host else 'yahoo' if 'yahoo.com' in host else 'screener' if 'screener.in' in host else None
    gate=LOCKS.get(group)
    if gate:gate.acquire()
    try:
        headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/html,*/*'}
        if payload is not None:headers.update({'Content-Type':'application/json','Origin':'https://scanx.trade'})
        for attempt in range(3):
            req=web.Request(address,data=None if payload is None else json.dumps(payload).encode(),headers=headers)
            try:
                with web.urlopen(req,timeout=20) as r:return r.read()
            except urllib.error.HTTPError as e:
                if attempt==2 or e.code not in (429,500,502,503,504):raise
                wait=e.headers.get('Retry-After',str(2**(attempt+1)))
                if not wait.isdigit() or int(wait)>60:raise
                time.sleep(max(1,int(wait)))
    finally:
        if gate:gate.release()

def cached_json(folder,key,ttl,loader):
    path=CACHE/folder/(key+'.json.gz');now=time.time()
    if path.exists() and 0<=now-path.stat().st_mtime<ttl:
        try:return json.loads(gzip.decompress(path.read_bytes()))
        except (OSError,ValueError):pass
    value=loader();path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_bytes(gzip.compress(json.dumps(value,separators=(',',':')).encode()));temp.replace(path)
    return value

def load_scanx_caps(official_isins):
    query={'data':{'sorder':'desc','sort':'Mcap','count':1000,'fields':['Sym','Isin','Mcap','Exch','Sid','Seosym'],'params':[{'field':'Exch','op':'','val':'NSE'}],'logic_op':'AND','pgno':1}}
    data=cached_json('universe','scanx-caps',6*3600,lambda:json.loads(read_bytes(CAP_API,query)))
    if data.get('code')!=0 or not isinstance(data.get('data'),list):raise ValueError('ScanX market-cap source unavailable')
    result={};official_by_isin={v:k for k,v in official_isins.items()}
    if len(official_by_isin)!=len(official_isins):raise ValueError('Official stock ISINs are not unique')
    for row in data['data']:
        if not isinstance(row,dict):raise ValueError('ScanX market-cap schema changed')
        symbol=row.get('Sym');isin=row.get('Isin')
        if row.get('Exch')!='NSE' or isin not in official_by_isin or not re.fullmatch(r'[A-Z0-9&.\-]+',str(symbol)):continue
        canonical=official_by_isin[isin]
        cap=Decimal(str(row.get('Mcap')))
        if not cap.is_finite() or cap<=0:continue
        value={'cap':str(cap),'source':CAP_API,'stock_isin':isin,'provider_symbol':symbol,'units':'INR crore','provider_asof':data.get('last_resp_time')}
        if canonical in result and result[canonical]!=value:raise ValueError('Conflicting ScanX market-cap records')
        result[canonical]=value
    return result,{'source':CAP_API,'exact_isin_matches':len(result),'symbol_aliases':{k:v['provider_symbol'] for k,v in result.items() if k!=v['provider_symbol']},'official_universe':len(official_isins),'fallback_symbols':sorted(set(official_isins)-set(result)),'scope':'Provider top 1000 by market cap; each universe member individually verified by its exact stock ISIN on NSE. Unique same-ISIN symbol changes are recorded as aliases. Missing identities use the independent Screener check.'}

def market_cap(symbol):
    if symbol in CAP_LOOKUP:return CAP_LOOKUP[symbol]
    def load():
        target=BASE+'/company/'+url.quote(symbol,safe='')+'/'
        try:html=read_bytes(target)
        except urllib.error.HTTPError as e:
            if e.code!=404:raise
            html=read_bytes(target+'consolidated/')
        soup=BeautifulSoup(html,'html.parser')
        names=[re.sub(r'\s+','',a.get_text(' ',strip=True)) for a in soup.select('a[href]')]
        if 'NSE:'+symbol not in names:raise ValueError('Market-cap company identity mismatch')
        for li in soup.select('#top-ratios li'):
            m=re.fullmatch(r'Market Cap\s*₹\s*([\d,.]+)\s*Cr\.',li.get_text(' ',strip=True))
            if m:return {'cap':str(Decimal(m[1].replace(',',''))),'source':target}
        raise ValueError('Market cap unavailable; no threshold inferred')
    return cached_json('cap',symbol,86400,load)

def mf_page_items(data,isin,today):
    """Use the provider's explicitly dated SHARE QUANTITIES, not allocation weights."""
    expected=['amcid','sch','planid','isin','shares','assetPerc','onemonthSharesCh','onemonthCh','threemonthCh','date','seo']
    if data.get('code')!=0 or data.get('headers')!=expected:raise ValueError('MF provider schema/code changed')
    result=[];identities=set()
    for row in data.get('data',[]):
        if not isinstance(row,list) or len(row)!=len(expected):raise ValueError('MF row/header mismatch')
        r=dict(zip(expected,row));key=str(r['planid'])+':'+str(r['isin'])
        if key in identities:raise ValueError('MF scheme duplicated within page')
        identities.add(key)
        dates=str(r['date']).split('|');quantities=str(r['shares']).split('|')
        if len(dates)!=len(quantities) or len(dates)<2:raise ValueError('MF monthly history incomplete')
        months={}
        for date,quantity in zip(dates,quantities):
            if date in months and Decimal(months[date])!=Decimal(quantity):raise ValueError('MF conflicting quantities for one month')
            months[date]=quantity
        if len(months)<2:raise ValueError('MF two distinct reporting months required')
        pairs=sorted(months.items(),reverse=True);new,old=pairs[:2]
        current=month_end(day(new[0]+'-01'));previous=month_end(day(old[0]+'-01'))
        if (current.year-previous.year)*12+current.month-previous.month!=1 or not 0<=(today-current).days<=62:raise ValueError('MF reporting dates stale/nonconsecutive/future')
        a,b=Decimal(old[1]),Decimal(new[1]);delta=Decimal(str(r['onemonthSharesCh']))
        if any(not x.is_finite() or x<0 or x!=x.to_integral_value() for x in (a,b)) or not delta.is_finite() or (b-a!=delta and not (len(dates)>len(months) and delta==0)):raise ValueError('MF quantity reconciliation failed')
        if b>a:
            result.append({'kind':'MF monthly quantity increase','buyer':str(r['sch']),'amc_id':str(r['amcid']),'scheme_id':str(r['planid']),'scheme_isin':str(r['isin']),
                'start':previous.isoformat(),'end':current.isoformat(),'previous_qty':int(a),'qty':int(b),'change':int(b-a),'provider_change':str(delta),'change_basis':'Difference of distinct dated monthly quantities; identical duplicate months collapsed',
                'source':'https://scanx.trade/','source_api':MF_API,'stock_isin':isin,
                'caveat':'Month-end reported quantity increase; not a confirmed trade today. Corporate actions/transfers and new schemes can affect quantities.'})
    return result,identities

def mf_for_stock(isin,today):
    def load():
        evidence=[];schemes=set();pages=0;total=None;seen=set()
        while True:
            pages+=1
            if pages>100:raise ValueError('MF pagination limit; coverage incomplete')
            data=json.loads(read_bytes(MF_API,{'data':{'isin':isin,'page':pages,'pageSize':1000}}))
            if data.get('code')!=0:raise ValueError('MF source unavailable')
            # An explicitly empty, successful provider result is distinct from a failed request.
            if not data.get('data') and int(data.get('totalcount',0))==0:
                return {'evidence':[],'schemes':0,'pages':pages,'complete':True,'source':MF_API}
            items,ids=mf_page_items(data,isin,today)
            if schemes&ids:raise ValueError('MF pages repeat scheme identities')
            schemes|=ids;evidence.extend(items)
            count=int(data['totalcount']);number=int(data['totalpages'])
            if total is not None and count!=total:raise ValueError('MF dataset changed during pagination; retry next scan')
            total=count
            if pages>=number:break
            if not ids:raise ValueError('MF pagination ended before total count')
        if len(schemes)!=total:raise ValueError('MF pagination count incomplete')
        return {'evidence':evidence,'schemes':len(schemes),'pages':pages,'complete':True,'source':MF_API}
    return cached_json('mf',isin,6*3600,load)

def merge_charts(base,recent):
    a=base['chart']['result'][0];b=recent['chart']['result'][0]
    if a['meta'].get('symbol')!=b['meta'].get('symbol'):raise ValueError('Cached price identity mismatch')
    bars={}
    for r in (a,b):
        if r['meta'].get('dataGranularity')!='1d':raise ValueError('Non-daily cached OHLC')
        q=r['indicators']['quote'][0]
        for i,ts in enumerate(r.get('timestamp',[])):bars[ts]=[q[k][i] for k in ('open','high','low','close')]
    timestamps=sorted(bars)
    quote={k:[bars[t][i] for t in timestamps] for i,k in enumerate(('open','high','low','close'))}
    events={**a.get('events',{}).get('splits',{}),**b.get('events',{}).get('splits',{})}
    return {'chart':{'result':[{'meta':b['meta'],'timestamp':timestamps,'indicators':{'quote':[quote]},'events':{'splits':events}}]}}

def prices(symbol,exchange="NSE"):
    suffix={'NSE':'.NS','BSE':'.BO'}[exchange];cache_key=symbol+('-BSE' if exchange=='BSE' else '')
    stem='https://query1.finance.yahoo.com/v8/finance/chart/'+url.quote(symbol+suffix,safe='')
    def history():return json.loads(read_bytes(stem+'?interval=1d&period1=946684800&period2='+str(int(time.time()))+'&events=splits'))
    cache_file=CACHE/'prices'/(cache_key+'.json.gz')
    had_fresh_cache=cache_file.exists() and 0<=time.time()-cache_file.stat().st_mtime<18*3600
    base=cached_json('prices',cache_key,18*3600,history)
    if not had_fresh_cache:return base,stem
    recent=json.loads(read_bytes(stem+'?interval=1d&range=5d&events=splits'))
    return merge_charts(base,recent),stem

def price_payload(obj,symbol,now,exchange="NSE"):
    r=obj['chart']['result'][0];meta=r['meta'];now_ts=now.timestamp()
    if meta.get('symbol')!=symbol+{'NSE':'.NS','BSE':'.BO'}[exchange] or meta.get('currency')!='INR' or meta.get('dataGranularity')!='1d':raise ValueError('Price identity/currency/granularity mismatch')
    session=meta.get('currentTradingPeriod',{}).get('regular',{})
    start,end=session.get('start',0),session.get('end',0)
    qt=meta.get('regularMarketTime',0);qp=meta.get('regularMarketPrice')
    live=bool(start<=now_ts<=end and 0<=now_ts-qt<=20*60 and isinstance(qp,(float,int)) and math.isfinite(qp) and qp>0)
    q=r['indicators']['quote'][0];closed=[];current=None;invalid=[];rounding=[]
    for i,ts in enumerate(r.get('timestamp',[])):
        dt=datetime.fromtimestamp(ts,IST).date()
        if dt>now.date():continue
        values=[q[k][i] for k in ('open','high','low','close')]
        if any(v is None or not isinstance(v,(float,int)) or not math.isfinite(v) or v<=0 for v in values):invalid.append(dt.isoformat());continue
        o,h,l,c=values;tol=max(values)*1e-6
        if l>min(o,c) or h<max(o,c) or h<l:
            if max(l-min(o,c),max(o,c)-h,l-h)>tol:invalid.append(dt.isoformat());continue
            h=max(h,o,c);l=min(l,o,c);rounding.append(dt.isoformat())
        row=[dt.isoformat(),o,h,l,c]
        complete=dt<now.date() or (end and now_ts>=end+900 and datetime.fromtimestamp(end,IST).date()==dt)
        if complete:closed.append(row)
        elif dt==now.date():current=row+[False]
    if len(closed)<60 or not 0<=(now.date()-day(closed[-1][0])).days<=10:raise ValueError('Insufficient or stale completed daily history')
    if closed[-1][0] in invalid:raise ValueError('Last completed price is invalid')
    rows=closed.copy();asof=closed[-1][0]
    quote={'price':closed[-1][4],'kind':'Last completed close','asof':asof}
    if live and current:
        rows.append(current);asof=current[0]
        quote={'price':qp,'kind':'Live session quote (may be delayed)','asof':datetime.fromtimestamp(qt,IST).isoformat()}
    splits=[datetime.fromtimestamp(x['date'],IST).date().isoformat() for x in r.get('events',{}).get('splits',{}).values()]
    return {'symbol':symbol,'timeframe':'Daily','rows':rows,'asof':asof,'quote':quote,'dropFirstAggregate':True,'missingDates':invalid},splits,{'invalid_rows':invalid,'rounding_repairs':rounding,'history_start':closed[0][0],'completed_asof':closed[-1][0],'quote':quote,'latest_quote_date':datetime.fromtimestamp(qt,IST).date().isoformat() if qt else None,'current_alerts_eligible':not any(d>asof for d in invalid) and (not qt or asof>=datetime.fromtimestamp(qt,IST).date().isoformat())}

def following_page(html,current):
    soup=BeautifulSoup(html,'html.parser');parsed=url.urlparse(current);candidates=[]
    for a in soup.select('a[href]'):
        label=a.get_text(' ',strip=True).lower();aria=a.get('aria-label','').lower()
        if 'next' in a.get('rel',[]) or re.fullmatch(r'next\s*[→»›]?',label) or aria=='next':candidates.append(a['href'])
    number=1
    for s in soup.select('.paginator .this-page, .pagination .current, [aria-current="page"]'):
        if s.get_text(strip=True).isdigit():number=int(s.get_text(strip=True))
    for a in soup.select('.paginator a[href], .pagination a[href]'):
        if a.get_text(strip=True)==str(number+1):candidates.append(a['href'])
    for href in candidates:
        target=url.urljoin(current,href);p=url.urlparse(target)
        if p.scheme=='https' and p.hostname==parsed.hostname and p.path==parsed.path and target!=current:return target
    return None

def disclosure_day(cell):
    # Prefer the source's machine-readable absolute date over labels such as "yesterday".
    candidates=[cell.get_text(' ',strip=True)]
    attributes=('datetime','title','data-date','data-iso-date','data-sort','data-sort-value','data-order')
    for element in [cell]+list(cell.find_all(True)):
        candidates.extend(str(element[k]) for k in attributes if element.get(k))
    dates=set()
    for raw in candidates:
        cleaned=re.sub(r'\b([A-Za-z]{3,4})\.',r'\1',raw).replace('Sept ','Sep ').replace(',',' ')
        try:dates.add(day(cleaned));continue
        except ValueError:pass
        try:
            stamp=datetime.fromisoformat(raw.replace('Z','+00:00'))
            dates.add((stamp.astimezone(IST) if stamp.tzinfo else stamp).date())
        except ValueError:pass
    if len(dates)==1:return dates.pop()
    if dates:raise ValueError('Conflicting absolute disclosure dates')
    metadata=[{k:str(e[k])[:80] for k in attributes if e.get(k)} for e in [cell]+list(cell.find_all(True))]
    print('DISCLOSURE DATE ATTRIBUTES',metadata,flush=True)
    raise ValueError('Unrecognised disclosure date: '+candidates[0][:50])

def disclosure_dates(html,normalize=False):
    soup=BeautifulSoup(html,'html.parser');table=soup.select_one('table#result_list') or soup.select_one('main table')
    if table is None:raise ValueError('Disclosure table unavailable')
    heads=[h.get_text(' ',strip=True).lower() for h in table.select('thead th')]
    names={'date','trade date','transaction date','acquisition date','disclosure date'}
    index=next((i for i,h in enumerate(heads) if h in names),None)
    if index is None:raise ValueError('Disclosure date column unavailable')
    dates=[]
    for row in table.select('tbody tr'):
        cells=row.find_all(['td','th'],recursive=False)
        if len(cells)==len(heads):
            raw=cells[index].get_text(' ',strip=True)
            parsed=disclosure_day(cells[index])
            dates.append(parsed)
            if normalize:cells[index].clear();cells[index].append(parsed.isoformat())
    return (str(soup),dates) if normalize else dates

def disclosure_page(client,target):
    for attempt in range(3):
        try:
            with DISCLOSURE_GATE:
                time.sleep(.6)
                with client.open(target,timeout=20) as response:
                    if url.urlparse(response.url).path!=url.urlparse(target).path:raise ValueError('Disclosure access redirected')
                    return response.read().decode()
        except urllib.error.HTTPError as e:
            wait=e.headers.get('Retry-After','15')
            if e.code!=429 or attempt==2 or not wait.isdigit() or int(wait)>60:raise
            time.sleep(max(1,int(wait)))

def read_disclosures(kind,path,symbols,today,deadline):
    client=login();target=BASE+path;seen=set();records=[];rows=0;last=None;descending=True
    cutoff=today-timedelta(days=45);reason=''
    complete=True
    try:
        while target:
            if time.monotonic()>deadline:raise ValueError('Disclosure time limit; recent-history coverage incomplete')
            if target in seen:raise ValueError('Disclosure pagination loop')
            if len(seen)>=500:raise ValueError('Disclosure pagination limit; coverage incomplete')
            html=disclosure_page(client,target)
            seen.add(target);html,dates=disclosure_dates(html,normalize=True);rows+=len(dates)
            if len(seen)%25==0:print('DISCLOSURE',kind,'pages',len(seen),'rows',rows,'oldest',str(min(dates)) if dates else 'none',flush=True)
            descending=descending and all(a>=b for a,b in zip(dates,dates[1:])) and (last is None or not dates or last>=max(dates))
            if dates:last=min(dates)
            records.extend(trade_rows(html,kind,symbols,today,BASE+url.urlparse(path).path))
            nxt=following_page(html,target)
            if nxt is None:reason='all available pages';break
            if dates and max(dates)<cutoff and descending:reason='all pages through the 45-day acquisition window';break
            target=nxt;time.sleep(.15)
    except Exception as e:
        complete=False;reason=type(e).__name__+': '+str(e)[:160]
    unique={hashlib.sha256(json.dumps([s,e],sort_keys=True).encode()).hexdigest():(s,e) for s,e in records}
    return list(unique.values()),{'pages':len(seen),'rows':rows,'complete':complete,'scope':reason,'start':cutoff.isoformat(),'end':today.isoformat()}

class CompactStore(Store):
    def read(self):
        super().read()
        if self.state.get('encoding')=='gzip-base64':
            raw=gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(self.state['data']))).read(20_000_001)
            if len(raw)>20_000_000:raise ValueError('Expanded state size limit')
            self.state=json.loads(raw)
        if not all(isinstance(self.state.get(k),t) for k,t in [('records',dict),('pending',list),('delivered',list)]):raise ValueError('Delivery state invalid; refusing to reset history')
    def save(self):
        raw=json.dumps(self.state,separators=(',',':')).encode();original=self.state
        try:
            if len(raw)>450000:self.state={'version':1,'encoding':'gzip-base64','data':base64.b64encode(gzip.compress(raw)).decode(),'last_batch':original.get('last_batch'),'destination':original.get('destination')}
            super().save()
        finally:self.state=original

def scan_stock(symbol,isin,now=None):
    if symbol.startswith('DUMMY') and isin.startswith('DUM'):return {'symbol':symbol,'excluded':'Official index placeholder without a tradable stock ISIN/price history'}
    cap=market_cap(symbol)
    if Decimal(cap['cap'])<=500:return {'symbol':symbol,'excluded':'Market cap <= INR 500 crore'}
    exchange='NSE'
    data_symbol=symbol  # Keep the official ticker's established price history across provider renames.
    verified_alias=YAHOO_QUOTE_ALIASES.get(symbol)
    if verified_alias and verified_alias[0]==isin:data_symbol=verified_alias[1]
    try:
        obj,source=prices(data_symbol);payload,splits,quality=price_payload(obj,data_symbol,now or datetime.now(IST))
    except (urllib.error.HTTPError,ValueError) as error:
        if isinstance(error,urllib.error.HTTPError) and error.code!=404:raise
        alternative=cap.get('provider_symbol',symbol)
        if verified_alias and verified_alias[0]==isin:
            # The same listed REIT has a separate BSE history. Never mix exchange candles.
            data_symbol=symbol;exchange='BSE'
            obj,source=prices(data_symbol,exchange);payload,splits,quality=price_payload(obj,data_symbol,now or datetime.now(IST),exchange)
        elif alternative!=data_symbol:
            data_symbol=alternative
            obj,source=prices(data_symbol);payload,splits,quality=price_payload(obj,data_symbol,now or datetime.now(IST))
        else:raise
    payload['symbol']=symbol;quality['data_symbol']=data_symbol;quality['stock_isin']=isin;quality['exchange']=exchange
    if exchange=='BSE':quality['fallback']='NSE daily history insufficient/unavailable; all demand bands and price use the separate BSE daily series.'
    run=subprocess.run(['node',str(ROOT/'zone_runner.cjs')],input=json.dumps(payload),capture_output=True,text=True,timeout=90,check=True)
    stock=json.loads(run.stdout)[0];stock.update({'price_source':source,'market_cap_cr':cap['cap'],'quality':quality,'evidence':[]})
    if stock['zones']:
        try:
            mf=mf_for_stock(isin,(now or datetime.now(IST)).date());stock['mf_status']={k:v for k,v in mf.items() if k!='evidence'};stock['_mf']=mf['evidence']
        except Exception as e:
            stock['mf_status']={'complete':False,'error':type(e).__name__+': '+str(e)[:160]};stock['_mf']=[]
    else:stock['mf_status']={'scope':'Not needed: price outside HTF demand zones'};stock['_mf']=[]
    stock['_splits']=splits
    return stock

def canonical_evidence(item):
    item=dict(item);parsed=url.urlparse(item.get('source',''))
    if parsed.hostname=='www.screener.in' and parsed.path.startswith('/trades/'):
        item['source']=url.urlunparse(parsed._replace(query='',fragment=''))
    return item

def match_identity(symbol,matched,canonical=True):
    stable=[symbol,[(z['tf'],z['date'],z.get('latestTouch'),sorted(hashlib.sha256(json.dumps(canonical_evidence(e) if canonical else e,sort_keys=True).encode()).hexdigest() for e in items)) for z,items in matched]]
    return 'full-v2:'+hashlib.sha256(json.dumps(stable,sort_keys=True).encode()).hexdigest()

def migrate_pagination_identities(state):
    delivered=set(state['delivered']);mapping={}
    for symbol,stock in state.get('records',{}).items():
        matched=[(e['zone'],e['evidence']) for e in stock.get('evidence',[])]
        if not matched:continue
        old=match_identity(symbol,matched,False);new=match_identity(symbol,matched)
        if old==new:continue
        mapping[old]=new
        if old in delivered and new not in delivered:state['delivered'].append(new);delivered.add(new)
    seen=set(delivered);pending=[]
    for item in state['pending']:
        item=dict(item);item['id']=mapping.get(item['id'],item['id'])
        if item['id'] not in seen:pending.append(item);seen.add(item['id'])
    state['pending']=pending

def alert_text(stock,matched):
    quote=stock['quality']['quote'];lines=['Institutional evidence + HTF demand',stock['symbol']+((' (quote symbol '+stock['quality']['data_symbol']+')') if stock['quality'].get('data_symbol',stock['symbol'])!=stock['symbol'] else '')+' | '+quote['kind']+' INR '+str(round(stock['cmp'],2)), 'Price as of '+quote['asof']+' | Exchange '+stock['quality'].get('exchange','NSE')]
    if stock['quality'].get('fallback'):lines.append(stock['quality']['fallback'])
    buyers={}
    for z,items in matched:
        lines.append(f"{z['tf']} demand {z['d']:.2f}–{z['p']:.2f} | formed {z['date']} | {'First return' if z['firstReturn'] else 'Tested context; entry confirmation required'} | visit {z.get('latestTouch')}")
        for item in items:buyers[hashlib.sha256(json.dumps(item,sort_keys=True).encode()).hexdigest()]=item
    for e in list(buyers.values())[:4]:
        measure='quantity '+str(e.get('qty','not provided'))
        if 'change' in e:measure+=' | increase '+str(e['change'])
        if 'reported_measure' in e:measure+=' | '+e['reported_measure']
        lines.extend([e['kind']+' | '+e['buyer'],e['start']+' → '+e['end']+' | '+measure,e['source']])
    if len(buyers)>4:lines.append(str(len(buyers)-4)+' additional qualifying scheme/acquisition records retained in scan state.')
    lines.extend(['Month-end quantity change is not a verified purchase today. Known splits suppress monthly MF matches; other non-trade changes remain possible.','Demand Lab v31 OHLC-derived bands; no analyst overrides inferred. Public quotes may be delayed.','Price: Yahoo Finance. MF: ScanX dated monthly quantities. Disclosed acquisitions: Screener.'])
    return '\n'.join(lines)[:3950]

def deliver_pending(store,destination,limit=100):
    if verify_private_destination()!=destination:raise ValueError('Private destination changed during delivery')
    state=store.state;sent=0
    while state['pending'] and sent<limit:
        item=state['pending'][0]
        response=json.loads(request('https://api.telegram.org/bot'+os.environ['BOT_TOKEN'].strip()+'/sendMessage',{'chat_id':os.environ['CHAT_ID'].strip(),'text':item['text'],'link_preview_options':{'is_disabled':True}}))
        if not response.get('ok'):raise ValueError('Telegram delivery rejected')
        state['delivered'].append(item['id']);state['pending'].pop(0);store.save();sent+=1;time.sleep(1.1)
    if sent:print('PRIVATE DELIVERY',sent,'queued alerts delivered; waiting',len(state['pending']),flush=True)
    return sent

def main():
    now=datetime.now(IST);dry='--dry-run' in sys.argv;destination=None if dry else verify_private_destination()
    raw=request(UNIVERSE_URL);symbols=universe_from_csv(raw);isin={r['Symbol'].strip():r['ISIN Code'].strip() for r in csv.DictReader(io.StringIO(raw))}
    # A sample is an explicit development mode; production always attempts the whole official universe.
    if os.environ.get('SCANNER_TEST_LIMIT'):symbols=symbols[:int(os.environ['SCANNER_TEST_LIMIT'])]
    store=None if dry else CompactStore('institutional-confluence-state.json')
    if store:store.read()
    state=store.state if store else {'version':1,'records':{},'pending':[],'delivered':[]}
    if destination and state.get('destination',{}).get('fingerprint')!=destination:raise ValueError('Private recipient must already be verified before full-universe scanning')
    migrate_pagination_identities(state)
    sent_earlier=deliver_pending(store,destination) if store and state['pending'] else 0
    coverage={};source_errors=[];trade_evidence={}
    try:
        cap_data,cap_status=load_scanx_caps(isin);CAP_LOOKUP.update(cap_data);coverage['Market cap']=cap_status
        print('MARKET CAPS exact official identities',len(cap_data),'/',len(isin),flush=True)
    except Exception as e:coverage['Market cap']={'source':CAP_API,'fallback':'Independent Screener identity checks','error':type(e).__name__+': '+str(e)[:120]}
    aliases={v['provider_symbol']:k for k,v in CAP_LOOKUP.items() if v['provider_symbol']!=k}
    trade_symbols=list(dict.fromkeys(symbols+list(aliases)))
    deadline=time.monotonic()+30*60
    if not dry:
        with ThreadPoolExecutor(max_workers=4) as executor:
            tasks={executor.submit(read_disclosures,kind,path,trade_symbols,now.date(),deadline):kind for kind,path in TRADE_PATHS.items()}
            for task in as_completed(tasks):
                kind=tasks[task]
                try:
                    records,status=task.result();coverage[kind]=status
                    if not status['complete']:source_errors.append(kind+': '+status['scope'])
                    for symbol,item in records:trade_evidence.setdefault(aliases.get(symbol,symbol),[]).append(item)
                except Exception as e:source_errors.append(kind+': '+type(e).__name__+' '+str(e)[:160]);coverage[kind]={'complete':False,'error':str(e)[:160]}
    try:
        amcs=json.loads(read_bytes(AMC_API));coverage['MF provider']={'source':AMC_API,'code':amcs.get('code'),'fund_houses':len(amcs.get('data',[])),'schemes':sum(len(a.get('scheme',[])) for a in amcs.get('data',[])),'scope':'Every returned scheme page for each stock currently inside an HTF demand zone; all fund houses supplied by the provider. Provider omissions cannot be ruled out.'}
        if amcs.get('code')!=0:raise ValueError('AMC catalogue unavailable')
    except Exception as e:source_errors.append('MF catalogue: '+type(e).__name__)
    known={}
    for value in state.get('trades',{}).values():
        symbol=value['symbol'];item=canonical_evidence(value['item'])
        known[hashlib.sha256(json.dumps([symbol,item],sort_keys=True).encode()).hexdigest()]={'symbol':symbol,'item':item}
    for symbol,items in trade_evidence.items():
        for e in items:known[hashlib.sha256(json.dumps([symbol,e],sort_keys=True).encode()).hexdigest()]={'symbol':symbol,'item':e}
    state['trades']={k:v for k,v in known.items() if 0<=(now.date()-day(v['item']['end'])).days<=45}
    for value in state['trades'].values():
        items=trade_evidence.setdefault(value['symbol'],[])
        if value['item'] not in items:items.append(value['item'])
    checked=[];excluded=[];exclusion_reasons={};suspended={};failed={};matches=[];mf_schemes=0;mf_pages=0;amc_ids=set();queued={p['id'] for p in state['pending']};delivered=set(state['delivered'])
    state['scan_progress']={'started_at':now.isoformat(),'universe_size':len(symbols),'attempted':0,'complete':False}
    if store:store.save()
    workers=min(8,max(1,int(os.environ.get('SCANNER_WORKERS','6'))))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        tasks={executor.submit(scan_stock,s,isin[s]):s for s in symbols}
        for task in as_completed(tasks):
            symbol=tasks[task]
            try:
                stock=task.result();checked.append(symbol)
                if stock.get('excluded'):excluded.append(symbol);exclusion_reasons[symbol]=stock['excluded'];state['records'].pop(symbol,None)
                else:
                    if stock['mf_status'].get('complete') is False:source_errors.append(symbol+' MF: '+stock['mf_status']['error'])
                    mf=stock.pop('_mf');splits=stock.pop('_splits');mf_schemes+=stock['mf_status'].get('schemes',0);mf_pages+=stock['mf_status'].get('pages',0)
                    amc_ids.update(e['amc_id'] for e in mf)
                    evidence=mf+trade_evidence.get(symbol,[]);matched=[]
                    if not stock['quality'].get('current_alerts_eligible',True):suspended[symbol]={'validated_close_asof':stock['quality']['completed_asof'],'latest_source_quote_date':stock['quality']['latest_quote_date'],'reason':'A newer source session lacks a validated daily bar; current-price alerts are suspended.'}
                    for z in stock['zones'] if symbol not in suspended else []:
                        items=[e for e in evidence if overlaps(z,e,stock['asof'],splits)]
                        if items:matched.append((z,items));stock['evidence'].append({'zone':z,'evidence':items})
                    if matched:
                        matches.append(symbol)
                        # Route-specific history is preserved. Source identity changes, not price ticks, create a new alert.
                        identity=match_identity(symbol,matched)
                        if identity not in queued and identity not in delivered:state['pending'].append({'id':identity,'text':alert_text(stock,matched)});queued.add(identity)
                    state['records'][symbol]=stock
                print('CHECKED',symbol,'inside',len(stock.get('zones',[])),'match',bool(stock.get('evidence')),flush=True)
            except Exception as e:
                failed[symbol]=type(e).__name__+': '+str(e)[:160]+((' | source '+str(url.urlparse(e.url).hostname)) if isinstance(e,urllib.error.HTTPError) else '');print('UNAVAILABLE',symbol,failed[symbol],flush=True)
            state['scan_progress']['attempted']+=1
            if store and state['scan_progress']['attempted']%50==0:store.save()
    state['scan_progress'].update({'complete':True,'finished_at':datetime.now(IST).isoformat(),'verified':len(checked),'unavailable':len(failed),'excluded':len(excluded)})
    state['retry_symbols']=list(failed);state['coverage']=coverage;state['last_batch']={'date':now.isoformat(),'checked':checked,'excluded':exclusion_reasons,'alerts_suspended':suspended,'failed':failed,'matching':matches,'source_errors':source_errors,'universe_size':len(symbols),'full_universe_attempted':True}
    state['mf_coverage']={'scheme_stock_rows':mf_schemes,'pages':mf_pages,'buying_amc_ids':sorted(amc_ids),'source':MF_API}
    sent=sent_earlier
    if store:
        store.save()
        sent+=deliver_pending(store,destination,max(0,100-sent))
    summary=f'Full universe attempted {len(symbols)}/{len(symbols)}; price/zone verified {len(checked)-len(excluded)}; explicitly excluded {len(excluded)}; current-price alerts suspended {len(suspended)}; unavailable {len(failed)}; matching stocks {len(matches)}; sent {sent}; waiting {len(state["pending"])}.'
    print(summary,flush=True)
    report={'summary':summary,'progress':state['scan_progress'],'coverage':coverage,'mf_coverage':state['mf_coverage'],'last_batch':state['last_batch'],'stocks':{s:state['records'][s] for s in checked if s in state['records']}}
    (ROOT/'full-scan-result.json').write_text(json.dumps(report,indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Full-universe institutional demand scan\n\n'+summary+'\n\nVerified private chat: Darpan Stock Alerts (@DarpanDemandAlerts_bot). Groups/channels blocked.\n\n')
            f.write('Live session quote when current and available; otherwise the last completed session close, including Friday over weekends. Public quotes may be delayed. Open HTF candles do not create completed formations.\n\n')
            f.write(f'Multi-AMC scheme-stock rows checked for stocks inside HTF demand: {mf_schemes}; MF pages {mf_pages}. Changes are calculated from distinct dated monthly quantities. Identical repeated months are collapsed; a zero displayed delta caused by these repetitions is not used as the change. External source omissions cannot be guaranteed away.\n\n')
            for k,v in coverage.items():f.write('- '+k+': '+json.dumps(v)+'\n')
            for symbol,reason in suspended.items():f.write('- ALERTS SUSPENDED '+symbol+': '+json.dumps(reason)+'\n')
            for symbol,reason in exclusion_reasons.items():f.write('- EXCLUDED '+symbol+': '+reason+'\n')
            for symbol,error in failed.items():f.write('- UNAVAILABLE '+symbol+': '+error+'\n')
            for error in source_errors:f.write('- SOURCE ERROR: '+error+'\n')
            f.write('\nMonthly quantity changes are not proof of same-day exchange purchases. All alerts remain dated research matches, with tested contexts separately labelled.\n')
    if failed or source_errors:raise SystemExit('Full universe attempted; unavailable external data explicitly reported. Valid alerts and queue preserved.')

if __name__=='__main__':main()
