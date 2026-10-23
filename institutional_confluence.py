"""Dated acquisition evidence overlapping a higher-timeframe demand-zone visit.
Monthly quantity changes are portfolio evidence, never a confirmed market trade.
"""
import base64, calendar, csv, hashlib, http.cookiejar, io, json, math, os, re, subprocess, sys, time
import urllib.error, urllib.parse as url, urllib.request as web
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from company_holdings import request, UNIVERSE_URL, universe_from_csv, read_company

BASE='https://www.screener.in'
NIPPON='https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures'
IST=ZoneInfo('Asia/Kolkata')
TRADE_PATHS={'Bulk':'/trades/bulk/?o=-2','Block':'/trades/block/?o=-2','SAST':'/trades/sast/?o=-2','Insider':'/trades/insiders/?o=-2'}

def binary(address):
    with web.urlopen(web.Request(address,headers={'User-Agent':'Mozilla/5.0'}),timeout=40) as r:return r.read()

def day(text):
    text=re.sub(r'\s+',' ',text.strip()).replace(',', ' ')
    for fmt in ('%Y-%m-%d','%d %b %Y','%d %B %Y','%b %d %Y','%B %d %Y','%d/%m/%Y','%d-%m-%Y','%d-%b-%Y'):
        try:return datetime.strptime(text,fmt).date()
        except ValueError:pass
    raise ValueError('Unrecognised source date')

def month_end(d):return d.replace(day=calendar.monthrange(d.year,d.month)[1])

def nippon_links(html,today):
    found={}
    for a in BeautifulSoup(html,'html.parser').select('a[href]'):
        target=url.urljoin(NIPPON,a['href']);p=url.urlparse(target)
        m=re.search(r'NIMF-MONTHLY-PORTFOLIO-(\d{2}-[A-Za-z]{3}-\d{2})\.xls[x]?$',p.path,re.I)
        if m and p.scheme=='https' and p.hostname=='mf.nipponindiaim.com':
            dt=datetime.strptime(m[1],'%d-%b-%y').date()
            if dt==month_end(dt) and dt<=today:found[dt]=target
    periods=sorted(found,reverse=True)[:2]
    if len(periods)!=2 or (periods[0].year-periods[1].year)*12+periods[0].month-periods[1].month!=1 or (today-periods[0]).days>62:raise ValueError('Two recent consecutive monthly disclosures required')
    return [(d,found[d]) for d in periods]

def workbook_rows(data):
    if data[:2]==b'PK':
        import openpyxl
        book=openpyxl.load_workbook(io.BytesIO(data),read_only=True,data_only=True)
        try:
            for sheet in book:yield sheet.title,list(sheet.values)
        finally:book.close()
    else:
        import xlrd
        book=xlrd.open_workbook(file_contents=data)
        for sheet in book.sheets():yield sheet.name,[sheet.row_values(i) for i in range(sheet.nrows)]

def portfolio(data,period,isin_map):
    result={};schemes=0
    for sheet,rows in workbook_rows(data):
        if sheet.lower()=='index':continue
        heading=None
        for i,row in enumerate(rows[:30]):
            labels=[re.sub(r'\s+',' ',str(x or '')).strip().lower() for x in row]
            if 'isin' in labels and 'quantity' in labels:
                heading=(i,labels.index('isin'),labels.index('quantity'));break
        if heading is None:continue
        description=' '.join(str(c or '') for row in rows[:heading[0]] for c in row)
        m=re.search(r'(?:as on|as at)\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})',description,re.I)
        if not m or day(m[1])!=period:raise ValueError('Workbook statement period missing/mismatched')
        scheme=str(rows[0][1] or sheet).split('(An open')[0].strip()
        i,ic,qc=heading;values={}
        for row in rows[i+1:]:
            if len(row)<=max(ic,qc):continue
            isin=str(row[ic] or '').strip()
            if isin not in isin_map:continue
            quantity=Decimal(str(row[qc]).replace(',',''))
            if not quantity.is_finite() or quantity<0 or quantity!=quantity.to_integral_value():raise ValueError('Invalid share quantity')
            if isin in values:raise ValueError('Duplicate equity ISIN within scheme')
            values[isin]=int(quantity)
        result[sheet]={'name':scheme,'values':values};schemes+=1
    if schemes<10:raise ValueError('Combined portfolio coverage unexpectedly small')
    return result

def compare_portfolios(old,new,start,end,isin_map,sources):
    evidence={}
    for scheme in sorted(set(old)&set(new)):
        a,b=old[scheme],new[scheme]
        if a['name']!=b['name']:continue # Renamed/merged schemes need review.
        for isin,qty in b['values'].items():
            prev=a['values'].get(isin,0)
            if qty>prev:
                evidence.setdefault(isin_map[isin],[]).append({'kind':'MF monthly quantity increase','buyer':b['name'],'start':start.isoformat(),'end':end.isoformat(),'previous_qty':prev,'qty':qty,'change':qty-prev,'source':sources[1],'previous_source':sources[0], 'caveat':'Month-end quantity increase; corporate actions/transfers can affect quantities; not a confirmed trade.'})
    return evidence

def login():
    client=web.build_opener(web.HTTPCookieProcessor(http.cookiejar.CookieJar()));client.addheaders=[('User-Agent','Mozilla/5.0')]
    with client.open(BASE+'/login/',timeout=20) as r:csrf=BeautifulSoup(r.read(),'html.parser').select_one('input[name="csrfmiddlewaretoken"]')
    if not csrf:raise ValueError('Login token missing')
    data=url.urlencode({'username':os.environ['SCREENER_EMAIL'].strip(),'password':os.environ['SCREENER_PASSWORD'],'csrfmiddlewaretoken':csrf['value'],'next':'/announcements/'}).encode()
    with client.open(web.Request(BASE+'/login/',data=data,headers={'Referer':BASE+'/login/'}),timeout=20) as r:r.read()
    return client

def trade_rows(html,kind,symbols,today,source):
    soup=BeautifulSoup(html,'html.parser');table=soup.select_one('table#result_list') or soup.select_one('main table')
    if table is None:raise ValueError('Trade table unavailable')
    heads=[re.sub(r'\s+',' ',h.get_text(' ',strip=True)).strip().lower() for h in table.select('thead th')]
    # Exact semantic columns only. Never infer acquisition from a positive quantity.
    def col(options):return next((i for i,h in enumerate(heads) if h in options),None)
    action=col({'trade type','transaction type','type','transaction','acquisition / disposal','acquisition/disposal','mode of acquisition','mode','buy/sell','buy / sell'})
    buyer=col({'client name','client','name','person','acquirer name','acquirer','name of acquirer','name of person','person name'})
    dated=col({'date','trade date','transaction date','acquisition date','disclosure date'})
    quantity=col({'quantity','shares','no. of shares','no of shares','securities acquired/disposed','shares traded','qty.','qty','number of shares','no. of securities'})
    measure=col({'value','percent'})
    if None in (action,buyer,dated) or quantity is None and measure is None:raise ValueError('Unrecognised trade columns: '+','.join(heads))
    output=[]
    for row in table.select('tbody tr'):
        cells=row.find_all(['td','th'],recursive=False)
        if len(cells)!=len(heads):continue
        text=[c.get_text(' ',strip=True) for c in cells]
        if text[action].strip().lower() not in {'buy','purchase','acquisition','acquisition (buy)','market purchase','open market purchase'}:continue
        links=[url.unquote(a['href'].split('/company/',1)[1].split('/')[0]) for a in row.select('a[href*="/company/"]')]
        syms=set(links)&set(symbols)
        if len(syms)!=1:continue
        dt=day(text[dated])
        if not 0<=(today-dt).days<=45:continue
        item={'kind':kind+' disclosed acquisition','buyer':text[buyer],'start':dt.isoformat(),'end':dt.isoformat(),'source':source,'caveat':'Disclosed acquisition; buyer category not inferred from name.'}
        if quantity is not None:
            q=Decimal(text[quantity].replace(',',''))
            if not q.is_finite() or q<=0:continue
            item['qty']=str(q)
        else:
            item['reported_measure']=heads[measure]+': '+text[measure]
            item['caveat']+=' Share quantity not provided on this source page; value/percent is shown exactly as reported.'
        output.append((syms.pop(),item))
    return output

def price_rows(obj,symbol,now):
    r=obj['chart']['result'][0];meta=r['meta']
    if meta.get('symbol')!=symbol+'.NS' or meta.get('currency')!='INR' or meta.get('dataGranularity')!='1d':raise ValueError('Price identity/granularity mismatch')
    quote=r['indicators']['quote'][0];rows=[];missing=[]
    for i,ts in enumerate(r['timestamp']):
        dt=datetime.fromtimestamp(ts,IST).date()
        if dt>now.date() or dt==now.date() and (now.hour,now.minute)<(16,0):continue
        values=[quote[k][i] for k in ('open','high','low','close')]
        if any(v is None or not math.isfinite(v) or v<=0 for v in values):missing.append(dt.isoformat());continue
        o,h,l,c=values
        if l>min(o,c) or h<max(o,c) or h<l:raise ValueError('Invalid OHLC')
        rows.append([dt.isoformat(),o,h,l,c])
    if len(rows)<60 or not 0<=(now.date()-day(rows[-1][0])).days<=7:raise ValueError('Insufficient/stale completed daily history')
    splits=[datetime.fromtimestamp(x['date'],IST).date().isoformat() for x in r.get('events',{}).get('splits',{}).values()]
    return {'symbol':symbol,'rows':rows,'asof':rows[-1][0],'timeframe':'Daily','dropFirstAggregate':True,'missingDates':missing},splits

def overlaps(zone,evidence,asof,splits):
    # Same current visit, and fully observed evidence period at decision time.
    touch=zone.get('latestTouch') or zone.get('firstTouch')
    if not touch or evidence['end']>asof or evidence['end']<touch[:10]:return False
    if evidence['kind'].startswith('MF') and any(evidence['start']<d<=evidence['end'] for d in splits):return False
    return True

class Store:
    def __init__(self,path):
        self.path=path;self.sha=None;self.state={'version':1,'cursor':0,'records':{},'pending':[],'delivered':[]}
        self.token=os.environ['GH_TOKEN'];self.branch=os.environ['GITHUB_REF_NAME'];self.endpoint='https://api.github.com/repos/'+os.environ['GITHUB_REPOSITORY']+'/contents/'+path
    def read(self):
        try:r=json.loads(request(self.endpoint+'?ref='+url.quote(self.branch,safe=''),token=self.token))
        except urllib.error.HTTPError as e:
            if e.code==404:return
            raise
        self.sha=r['sha'];self.state=json.loads(base64.b64decode(r['content']))
        if self.state.get('version')!=1:raise ValueError('State version unsupported')
    def save(self):
        raw=json.dumps(self.state,separators=(',',':')).encode()
        if len(raw)>850000:raise ValueError('State size limit')
        data={'branch':self.branch,'message':'Update institutional demand-zone scan state','content':base64.b64encode(raw).decode()}
        if self.sha:data['sha']=self.sha
        self.sha=json.loads(request(self.endpoint,data,self.token,'PUT'))['content']['sha']

def main():
    now=datetime.now(IST);today=now.date();dry='--dry-run' in sys.argv
    raw=request(UNIVERSE_URL);symbols=universe_from_csv(raw)
    isin_map={r['ISIN Code'].strip():r['Symbol'].strip() for r in csv.DictReader(io.StringIO(raw))}
    store=None if dry else Store('institutional-confluence-state.json')
    if store:store.read()
    state=store.state if store else {'cursor':0,'records':{},'pending':[],'delivered':[]}
    errors=[];coverage={};evidence={}
    cache=None if dry else Store('mf-nippon-evidence.json')
    if cache:cache.read()
    if cache and cache.state.get('checked_date')==today.isoformat():
        evidence=cache.state.get('evidence',{});coverage['MF']=cache.state['coverage']
    else:
        try:
            links=nippon_links(binary(NIPPON),today)
            recent=portfolio(binary(links[0][1]),links[0][0],isin_map);old=portfolio(binary(links[1][1]),links[1][0],isin_map)
            evidence=compare_portfolios(old,recent,links[1][0],links[0][0],isin_map,[links[1][1],links[0][1]])
            coverage['MF']=f'Nippon only: {len(set(old)&set(recent))} comparable schemes; {links[1][0]} to {links[0][0]}; other AMCs not covered'
            if cache:cache.state={'version':1,'checked_date':today.isoformat(),'evidence':evidence,'coverage':coverage['MF']};cache.save()
        except Exception as e:errors.append('MF '+type(e).__name__+': '+str(e)[:170]);coverage['MF']='Unavailable; no MF matches inferred'
    if not dry:
        try:
            client=login()
            for kind,path in TRADE_PATHS.items():
                try:
                    target=BASE+path
                    with client.open(target,timeout=25) as r:
                        if url.urlparse(r.url).path!=url.urlparse(target).path:raise ValueError('Trade source redirected')
                        trades=trade_rows(r.read(),kind,symbols,today,target)
                    for symbol,item in trades:evidence.setdefault(symbol,[]).append(item)
                    coverage[kind]=f'{len(trades)} qualifying acquisitions; newest source page only'
                except Exception as e:errors.append(kind+' '+type(e).__name__+': '+str(e)[:220]);coverage[kind]='Unavailable'
        except Exception as e:errors.append('Trade login '+type(e).__name__)
    saved=state.setdefault('trades',{})
    for symbol,items in evidence.items():
        for item in items:
            if not item['kind'].startswith('MF'):
                saved[hashlib.sha256(json.dumps([symbol,item],sort_keys=True).encode()).hexdigest()]={'symbol':symbol,'item':item}
    state['trades']={k:v for k,v in saved.items() if 0<=(today-day(v['item']['end'])).days<=45}
    for v in state['trades'].values():
        items=evidence.setdefault(v['symbol'],[])
        if v['item'] not in items:items.append(v['item'])
    cursor=state['cursor']%len(symbols);limit=min(25,max(1,int(os.environ.get('CONFLUENCE_BATCH_SIZE','12'))))
    batch=[symbols[(cursor+i)%len(symbols)] for i in range(limit)];checked=[];failed=[];matches=[]
    # Revisit symbols that have dated evidence first, then progress across the universe.
    retry=state.get('retry_symbols',[])[:limit//3]
    batch=list(dict.fromkeys(retry+batch))[:limit]
    for symbol in batch:
        try:
            company=read_company(symbol)
            if company is None:
                checked.append(symbol);state['records'].pop(symbol,None);continue
            target='https://query1.finance.yahoo.com/v8/finance/chart/'+url.quote(symbol+'.NS',safe='')+'?interval=1d&period1=946684800&period2='+str(int(time.time()))+'&events=splits'
            payload,splits=price_rows(json.loads(binary(target)),symbol,now)
            run=subprocess.run(['node',str(Path(__file__).with_name('zone_runner.cjs'))],input=json.dumps(payload),capture_output=True,text=True,timeout=60,check=True)
            stock=json.loads(run.stdout)[0];stock['price_source']=target;stock['market_cap_cr']=company['market_cap_cr'];stock['evidence']=[]
            for z in stock['zones']:
                items=[e for e in evidence.get(symbol,[]) if overlaps(z,e,stock['asof'],splits)]
                if items:
                    found={'zone':z,'evidence':items[:8]};stock['evidence'].append(found);matches.append(symbol)
                    # Identity excludes quote/date of repeated scan, includes evidence and zone visit.
                    identity=hashlib.sha256(json.dumps([symbol,z['tf'],z['date'],z['latestTouch'],items],sort_keys=True).encode()).hexdigest()
                    if identity not in state['delivered'] and identity not in {p['id'] for p in state['pending']}:
                        lines=['Institutional evidence + HTF demand',symbol+' | completed close INR '+str(round(stock['cmp'],2))+' | '+stock['asof'],f"{z['tf']} demand: {z['d']:.2f}–{z['p']:.2f} | formed {z['date']}",f"Current visit since {z['latestTouch']} | tests {z['testEpisodes']}",'First return' if z['firstReturn'] else 'Previously tested HTF context — confirmation review required']
                        for e in items[:3]:
                            lines.extend([e['kind']+' | '+e['buyer'],e['start']+' → '+e['end']+' | quantity '+str(e.get('qty','not provided'))+((' | increase '+str(e['change'])) if 'change' in e else '')+(' | reported '+e['reported_measure'] if e.get('reported_measure') else ''),e['caveat'],e['source']])
                        lines.extend(['Demand Lab v31 OHLC-derived zone; no manual marking inferred.','Daily completed close; this is not an intraday trigger.','Price: Yahoo Finance | https://finance.yahoo.com/quote/'+url.quote(symbol+'.NS',safe='')+'/history/'])
                        state['pending'].append({'id':identity,'text':'\n'.join(lines)[:3950]})
            state['records'][symbol]=stock;checked.append(symbol);print('CHECKED',symbol,'inside HTF zones',len(stock['zones']),'matched',len(stock['evidence']),flush=True)
        except Exception as e:
            failed.append(symbol);print('PRICE/ZONE UNAVAILABLE',symbol,type(e).__name__,str(e)[:160],flush=True)
        time.sleep(1)
    state['cursor']=(cursor+max(1,limit-len(retry)))%len(symbols);state['retry_symbols']=list(dict.fromkeys(state.get('retry_symbols',[])+failed))
    state['retry_symbols']=[s for s in state['retry_symbols'] if s not in checked]
    state['coverage']=coverage;state['last_batch']={'date':now.isoformat(),'checked':checked,'failed':failed,'matching':list(dict.fromkeys(matches)),'source_errors':errors}
    sent=0
    if store:
        store.save()
        while state['pending'] and sent<15:
            item=state['pending'][0]
            r=json.loads(request('https://api.telegram.org/bot'+os.environ['BOT_TOKEN'].strip()+'/sendMessage',{'chat_id':os.environ['CHAT_ID'].strip(),'text':item['text'],'link_preview_options':{'is_disabled':True}}))
            if not r.get('ok'):raise ValueError('Telegram delivery failed')
            state['delivered'].append(item['id']);state['pending'].pop(0);store.save();sent+=1
    summary=f'HTF demand: checked {len(checked)}/{len(batch)}; confluence stocks {len(set(matches))}; sent {sent}; waiting {len(state["pending"])}; unavailable {len(failed)}.'
    print(summary);print(json.dumps(coverage));print('Source errors:',json.dumps(errors))
    Path('institutional-confluence-result.json').write_text(json.dumps({'summary':summary,'coverage':coverage,'last_batch':state['last_batch'],'stocks':{s:state['records'][s] for s in checked if s in state['records']}},indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Institutional buying evidence + higher-timeframe demand\n\n'+summary+'\n\nMonthly / Quarterly / Half Yearly / Yearly zones from Demand Lab v31. Completed daily OHLC, not intraday. Tested zones are context requiring entry confirmation.\n\n')
            for k,v in coverage.items():f.write('- '+k+': '+v+'\n')
            f.write('\nQuarterly FII/DII ownership remains separate and never triggers a same-day buying claim. MF quantity increases can include non-trade changes; known splits suppress confluence. Full AMC coverage is not available.\n\n')
            for s in checked:
                rec=state['records'].get(s,{})
                if rec.get('zones'):f.write('- '+s+': '+str(len(rec['zones']))+' inside HTF zones; '+str(len(rec['evidence']))+' overlapping evidence matches\n')
            for e in errors:f.write('\nSource unavailable: '+e+'\n')
    if failed or errors:raise SystemExit('Partial coverage: unavailable sources/prices listed; state and queue retained')

if __name__=='__main__':main()
