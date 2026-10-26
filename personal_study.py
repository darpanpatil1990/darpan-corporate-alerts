"""Darpan Personal STUDY: qualified entry + dated contextual observations.

Independent private summary. Original opportunity, entry and rotation alerts stay separate.
Sector/RS are advisory; entry qualification reuses the established scanner gates.
"""
import hashlib
import base64
import json
import math
import os
import re
import sys
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote
import calendar
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scanner_runtime import (CompactStore, combined_entry_alert, alert_text, read_bytes,
                             request, verify_private_destination, IST)
from institutional_confluence import Store
from sector_rotation import completed_friday

ARS_ANCHOR='2024-05-10'  # Explicit historical public reference, not claimed as the latest official anchor.

DISCLAIMER = ('⚠️ Disclaimer: This alert is for educational and research purposes only and is not a recommendation to buy or sell. '
              'Demand zones, institutional evidence, and sector strength do not guarantee future returns. '
              'Data may be delayed or incomplete. Entry, stop-loss, and target levels are study references, not execution instructions. '
              'Verify all information independently and consider your risk tolerance before trading. '
              'Investments in securities are subject to market risk.')


def seal(state,secret,aad):
    key=hashlib.sha256(('Darpan Personal STUDY state v1|'+secret).encode()).digest()
    nonce=os.urandom(12)
    ciphertext=AESGCM(key).encrypt(nonce,json.dumps(state,separators=(',',':')).encode(),aad.encode())
    return {'version':1,'encoding':'aes256gcm-base64','data':base64.b64encode(nonce+ciphertext).decode()}


def unseal(envelope,secret,aad):
    if envelope.get('encoding')!='aes256gcm-base64':
        raise ValueError('Encrypted study state required; history not reset')
    key=hashlib.sha256(('Darpan Personal STUDY state v1|'+secret).encode()).digest()
    raw=base64.b64decode(envelope['data'],validate=True)
    return json.loads(AESGCM(key).decrypt(raw[:12],raw[12:],aad.encode()))


class PrivateStore(Store):
    def read(self):
        super().read()
        if self.sha:
            self.state=unseal(self.state,os.environ['BOT_TOKEN'].strip(),self.endpoint)
    def save(self):
        state=self.state
        try:
            self.state=seal(state,os.environ['BOT_TOKEN'].strip(),self.endpoint)
            super().save()
        finally:
            self.state=state


def valid_snapshot(state, now):
    progress = state.get('scan_progress',{})
    if progress.get('complete') is not True:
        raise ValueError('Institutional scan still in progress; combined study deferred')
    finished = datetime.fromisoformat(progress['finished_at'])
    if finished.tzinfo is None or not 0 <= (now-finished).total_seconds() <= 7200:
        raise ValueError('Completed institutional snapshot older than two hours')
    return progress['finished_at']


def sector_context(stock, snapshot, now):
    if not snapshot:
        return 'Sector rotation: Unavailable (no structured snapshot)',[]
    created = datetime.fromisoformat(snapshot['generated_at'])
    if created.tzinfo is None or not 0 <= (now-created).total_seconds() <= 10*86400 or snapshot.get('week') != completed_friday(now).isoformat():
        return 'Sector rotation: Unavailable (latest completed weekly snapshot missing)',[]
    matched=[]
    for name,item in snapshot.get('related',{}).items():
        if item.get('stock_isins',{}).get(stock['symbol']) == stock['quality']['stock_isin']:
            row=snapshot['results'][name]
            matched.append((name,row['group'],row['previous']))
    if not matched:
        return 'Sector rotation: Unavailable (no verified membership in covered indices)',[]
    lines=['Sector rotation | week '+snapshot['week']]
    for name,group,previous in matched:
        change=(' | '+previous+' → '+group) if previous not in ('Unavailable',group) else ''
        lines.append(name.removeprefix('Nifty ')+': '+group+change)
    return '\n'.join(lines),matched


def daily_closes(raw, expected, asof):
    result = raw['chart']['result'][0]
    meta=result['meta']
    if meta.get('symbol') != expected or meta.get('currency') != 'INR' or meta.get('dataGranularity') != '1d':
        raise ValueError('Strength series identity/currency/interval mismatch')
    closes={}
    for timestamp,value in zip(result.get('timestamp',[]),result['indicators']['quote'][0]['close'],strict=True):
        day=datetime.fromtimestamp(timestamp,IST).date().isoformat()
        if day > asof:
            continue
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            continue
        if day in closes and closes[day] != value:
            raise ValueError('Conflicting daily strength closes')
        closes[day]=value
    if asof not in closes:
        raise ValueError('Strength source missing required completed session')
    return closes


def relative_returns(stock, benchmark, asof):
    # Use consecutive benchmark sessions; never shorten a lookback around missing stock data.
    dates=sorted(benchmark)
    if not dates or dates[-1]!=asof or asof not in stock:
        raise ValueError('Strength dates are not aligned')
    result={}
    for label,n in [('1W',5),('1M',21),('3M',63),('55 sessions',55)]:
        span=dates[-n-1:]
        if len(span)!=n+1 or any(d not in stock for d in span):
            result[label]=None
        else:
            first,last=span[0],span[-1]
            result[label]=100*((stock[last]/stock[first])/(benchmark[last]/benchmark[first])-1)
    return result


def rs_study_values(stock,benchmark,asof,anchor=ARS_ANCHOR):
    dates=sorted(benchmark)
    def value(n,offset=0):
        end=len(dates)-1-offset
        if end<n:return None
        span=dates[end-n:end+1]
        if len(span)!=n+1 or any(d not in stock for d in span):return None
        return 100*((stock[span[-1]]/stock[span[0]])/(benchmark[span[-1]]/benchmark[span[0]])-1)
    if not dates or dates[-1]!=asof:raise ValueError('RS study session mismatch')
    ars=None;prior_ars=None
    if anchor in stock and anchor in benchmark and asof in stock:
        ars=100*((stock[asof]/stock[anchor])/(benchmark[asof]/benchmark[anchor])-1)
        if len(dates)>1 and dates[-2] in stock:
            prior_ars=100*((stock[dates[-2]]/stock[anchor])/(benchmark[dates[-2]]/benchmark[anchor])-1)
    srs=value(123);prior_srs=value(123,1)
    fresh=None if any(v is None for v in (ars,srs,prior_ars,prior_srs)) else ars>0 and srs>0 and not(prior_ars>0 and prior_srs>0)
    return {'rs55':value(55),'srs123':srs,'ars':ars,'fresh_joint_cross':fresh}


def eps_from_html(raw,symbol,today):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(raw,'html.parser')
    names=[re.sub(r'\s+','',a.get_text(' ',strip=True)) for a in soup.select('a[href]')]
    if 'NSE:'+symbol not in names:raise ValueError('EPS company identity mismatch')
    table=soup.select_one('#quarters table')
    if table is None:raise ValueError('Quarterly EPS table unavailable')
    headers=table.select('thead tr th');periods=[]
    for i,cell in enumerate(headers):
        if i==0:continue
        text=cell.get_text(' ',strip=True)
        try:dt=datetime.strptime(text,'%b %Y').date()
        except ValueError:continue
        end=dt.replace(day=calendar.monthrange(dt.year,dt.month)[1])
        if dt.month in (3,6,9,12) and end<=today:periods.append((end,i))
    rows=[]
    for row in table.select('tbody tr'):
        cells=row.find_all(['th','td'],recursive=False)
        if cells and re.sub(r'\s+','',cells[0].get_text()).casefold() in ('epsinrs','epsinrs.','eps₹','eps'):
            rows.append(cells)
    if len(rows)!=1:raise ValueError('Quarterly EPS row ambiguous/missing')
    values={}
    for dt,i in periods:
        text=rows[0][i].get_text(' ',strip=True).replace(',','')
        if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):continue
        if dt in values:raise ValueError('Duplicate EPS period')
        values[dt]=Decimal(text)
    if not values:raise ValueError('EPS data unavailable')
    latest=max(values)
    if (today-latest).days>190:raise ValueError('EPS quarter stale')
    prior=latest.replace(year=latest.year-1)
    current,previous=values[latest],values.get(prior)
    if previous is None:status='YoY unavailable'
    elif previous>0:status=f'YoY {(current/previous-1)*100:+.2f}%'
    elif previous<0 and current>0:status='Loss → profit turnaround'
    elif previous<current<0:status='Loss reducing; still loss-making'
    else:status='YoY % not meaningful (non-positive prior EPS)'
    return {'quarter':latest.isoformat(),'eps':str(current),'comparison':status}


def eps_context(stock,now):
    source='https://www.screener.in/company/'+quote(stock['symbol'],safe='')+'/consolidated/'
    try:raw=read_bytes(source)
    except Exception as error:
        if getattr(error,'code',None)!=404:raise
        source=source.replace('/consolidated/','/')
        raw=read_bytes(source)
    result=eps_from_html(raw,stock['symbol'],now.date())
    return ('Quarterly EPS (reported): INR '+result['eps']+' | '+result['quarter']+' | '+result['comparison']+
            '\nEPS observed '+now.date().isoformat()+'; historical availability not inferred.\n'+source+'#quarters')


def strength_context(stock, benchmark_raw, nifty50_raw=None):
    ticker=stock['quality'].get('data_symbol',stock['symbol'])+'.NS'
    if stock['quality'].get('exchange','NSE')!='NSE':
        return 'Stock strength: Unavailable (separate exchange history)'
    asof=stock['quality']['completed_asof']
    stem='https://query1.finance.yahoo.com/v8/finance/chart/'
    raw=json.loads(read_bytes(stem+quote(ticker,safe='')+'?interval=1d&range=5y'))
    closes=daily_closes(raw,ticker,asof)
    benchmark=daily_closes(benchmark_raw,'^CRSLDX',asof)
    # An official final close must agree before mixing these source histories.
    if stock['quality']['quote']['kind']=='NSE official final close' and abs(closes[asof]/stock['cmp']-1)>0.001:
        raise ValueError('Stock RS close disagrees with verified NSE close')
    values=relative_returns(closes,benchmark,asof)
    text=('Stock relative return vs NIFTY 500 | '+asof+'\n'+
            ' | '.join(k+': '+('Unavailable' if v is None else f'{v:+.2f}%') for k,v in values.items())+
            '\nYahoo completed daily price returns; not stock/sector RS.')
    if nifty50_raw:
        rs=rs_study_values(closes,daily_closes(nifty50_raw,'^NSEI',asof),asof)
        display=lambda x:'Unavailable' if x is None else f'{x:+.2f}%'
        text+='\nRS study vs NIFTY 50 | RS55 '+display(rs['rs55'])+' | SRS123 '+display(rs['srs123'])+' | ARS '+display(rs['ars'])
        text+='\nARS anchor '+ARS_ANCHOR+' (historical public reference; latest official anchor unconfirmed).'
        text+='\nFresh joint ARS/SRS positive cross: '+('Unavailable' if rs['fresh_joint_cross'] is None else 'YES' if rs['fresh_joint_cross'] else 'NO')
    else:text+='\nARS / SRS / RS55: Unavailable (NIFTY 50 source unavailable).'
    return text


def study_alert(stock, snapshot, now, strength,eps='Quarterly EPS: Unavailable'):
    if stock.get('excluded') or not stock['quality'].get('current_alerts_eligible',True):
        return None
    cap=Decimal(str(stock['market_cap_cr']))
    if not cap.is_finite() or cap<=500:
        return None
    matched=[(e['zone'],e['evidence']) for e in stock.get('evidence',[])]
    entry=combined_entry_alert(stock,matched)
    if not entry:
        return None
    sector,groups=sector_context(stock,snapshot,now)
    entry_prefix=entry['text'].split('\n\nInstitutional evidence + HTF demand',1)[0]
    entry_prefix='\n'.join(entry_prefix.splitlines()[1:])
    entry_prefix='\n'.join('Ranking: HTF support → achievement → closing → distance/width.' if line.startswith('Selection:') else
        'Independent path gates passed; no cross-path borrowing.' if line.startswith('Each path passed') else
        'Unbuffered references; chart/risk review required. Not Entry Ready.' if line.startswith('Buffers, risk sizing') else line
        for line in entry_prefix.splitlines())
    header=(f"Darpan Personal STUDY\n{stock['symbol']} | NIFTY 500 | Market cap INR {cap:,.0f} Cr\n"+
            sector+'\n'+strength+'\n'+eps+'\n\n')
    prefix=header+entry_prefix+'\n\n'
    budget=4096-len(prefix)-len(DISCLAIMER)-4
    if budget < 650:
        raise ValueError('Combined study cannot fit complete references and disclaimer')
    evidence=alert_text(stock,matched,budget=budget,include_zones=False)
    text=prefix+evidence+'\n\n'+DISCLAIMER
    if len(text)>4096:
        raise ValueError('Combined study exceeds Telegram limit')
    # Quote ticks and strength changes alone do not create new setup alerts.
    identity='personal-study-v2:'+hashlib.sha256(json.dumps([entry['id'],groups,ARS_ANCHOR],sort_keys=True).encode()).hexdigest()
    return {'id':identity,'text':text,'symbol':stock['symbol'],'kind':'personal_study'}


def main():
    now=datetime.now(IST)
    dry='--dry-run' in sys.argv
    if dry and '--local-snapshot' in sys.argv:
        state=json.load(open(sys.argv[sys.argv.index('--local-snapshot')+1]))
    else:
        source=CompactStore('institutional-confluence-state.json');source.read();state=source.state
    verified=valid_snapshot(state,now)
    # A sector outage cannot block a qualified entry; unavailable context is explicit.
    if dry and '--local-sector' in sys.argv:
        snapshot=json.load(open(sys.argv[sys.argv.index('--local-sector')+1]))
    else:
        sector=Store('sector-rotation-state.json');sector.read();snapshot=sector.state.get('rotation_snapshot')
    candidates=[]
    for symbol in sorted(state.get('last_batch',{}).get('checked',[])):
        stock=state['records'].get(symbol)
        if not stock or stock.get('excluded') or stock['quality'].get('stock_isin') != state.get('universe_snapshot',{}).get('members',{}).get(symbol):
            continue
        matched=[(e['zone'],e['evidence']) for e in stock.get('evidence',[])]
        if matched and combined_entry_alert(stock,matched):
            candidates.append(stock)
    benchmark=None;nifty50=None
    if candidates:
        try:
            benchmark=json.loads(read_bytes('https://query1.finance.yahoo.com/v8/finance/chart/%5ECRSLDX?interval=1d&range=1y'))
        except (OSError,ValueError):
            pass
        try:nifty50=json.loads(read_bytes('https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1d&range=5y'))
        except (OSError,ValueError):pass
    alerts=[]
    for stock in candidates:
        try:
            strength=strength_context(stock,benchmark,nifty50) if benchmark else 'Stock strength: Unavailable (benchmark source unavailable)'
        except (OSError,ValueError,KeyError,TypeError):
            strength='Stock strength: Unavailable (source/date/identity check failed)'
        try:eps=eps_context(stock,now)
        except (OSError,ValueError,KeyError,TypeError):eps='Quarterly EPS: Unavailable (source/identity/period check failed)'
        alert=study_alert(stock,snapshot,now,strength,eps)
        if alert:
            alerts.append(alert)
            if dry:
                print(alert['text']+'\n',flush=True)
    if dry:
        print(f'PERSONAL STUDY DRY RUN | Qualified: {len(alerts)} | Source completed: {verified}')
        return
    from sector_rotation import deliver
    destination=verify_private_destination()
    store=PrivateStore('personal-study-state.json');store.read()
    # Rebuild unsent messages from the current verified snapshot; never deliver stale setups.
    current={a['id']:a for a in alerts}
    store.state['pending']=[current[p['id']] for p in store.state['pending'] if p['id'] in current]
    store.state['source_completed_at']=verified
    store.save()
    def send(message):
        return json.loads(request('https://api.telegram.org/bot'+os.environ['BOT_TOKEN'].strip()+'/sendMessage',
                                  {'chat_id':os.environ['CHAT_ID'].strip(),'text':message,'disable_web_page_preview':True}))
    sent=0
    for alert in alerts:
        sent+=deliver(store,alert['text'],alert['id'],destination,send)
    print(f'PERSONAL STUDY SUCCESS | Qualified: {len(alerts)} | Sent: {sent} | Waiting: {len(store.state["pending"])}',flush=True)


if __name__=='__main__':
    main()
