"""Darpan Personal STUDY: qualified entry + dated contextual observations.

Independent private summary. Original opportunity, entry and rotation alerts stay separate.
Sector/RS are advisory; entry qualification reuses the established scanner gates.
"""
import hashlib
import base64
import json
import math
import os
import sys
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scanner_runtime import (CompactStore, combined_entry_alert, alert_text, read_bytes,
                             request, verify_private_destination, IST)
from institutional_confluence import Store
from sector_rotation import completed_friday

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


def strength_context(stock, benchmark_raw):
    ticker=stock['quality'].get('data_symbol',stock['symbol'])+'.NS'
    if stock['quality'].get('exchange','NSE')!='NSE':
        return 'Stock strength: Unavailable (separate exchange history)'
    asof=stock['quality']['completed_asof']
    stem='https://query1.finance.yahoo.com/v8/finance/chart/'
    raw=json.loads(read_bytes(stem+quote(ticker,safe='')+'?interval=1d&range=1y'))
    closes=daily_closes(raw,ticker,asof)
    benchmark=daily_closes(benchmark_raw,'^CRSLDX',asof)
    # An official final close must agree before mixing these source histories.
    if stock['quality']['quote']['kind']=='NSE official final close' and abs(closes[asof]/stock['cmp']-1)>0.001:
        raise ValueError('Stock RS close disagrees with verified NSE close')
    values=relative_returns(closes,benchmark,asof)
    return ('Stock relative return vs NIFTY 500 | '+asof+'\n'+
            ' | '.join(k+': '+('Unavailable' if v is None else f'{v:+.2f}%') for k,v in values.items())+
            '\nPrice-return comparison; Yahoo daily history, not stock/sector RS.')


def study_alert(stock, snapshot, now, strength):
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
    header=(f"Darpan Personal STUDY\n{stock['symbol']} | NIFTY 500 | Market cap INR {cap:,.0f} Cr\n"+
            sector+'\n'+strength+'\nARS / SRS / EPS: Unavailable (dashboard feed not linked; no proxy values).\n\n')
    prefix=header+entry_prefix+'\n\n'
    budget=4096-len(prefix)-len(DISCLAIMER)-4
    if budget < 650:
        raise ValueError('Combined study cannot fit complete references and disclaimer')
    evidence=alert_text(stock,matched,budget=budget,include_zones=False)
    text=prefix+evidence+'\n\n'+DISCLAIMER
    if len(text)>4096:
        raise ValueError('Combined study exceeds Telegram limit')
    # Quote ticks and strength changes alone do not create new setup alerts.
    identity='personal-study-v1:'+hashlib.sha256(json.dumps([entry['id'],groups],sort_keys=True).encode()).hexdigest()
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
    benchmark=None
    if candidates:
        try:
            benchmark=json.loads(read_bytes('https://query1.finance.yahoo.com/v8/finance/chart/%5ECRSLDX?interval=1d&range=1y'))
        except (OSError,ValueError):
            pass
    alerts=[]
    for stock in candidates:
        try:
            strength=strength_context(stock,benchmark) if benchmark else 'Stock strength: Unavailable (benchmark source unavailable)'
        except (OSError,ValueError,KeyError,TypeError):
            strength='Stock strength: Unavailable (source/date/identity check failed)'
        alert=study_alert(stock,snapshot,now,strength)
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
