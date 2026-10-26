"""Independent weekly sector rotation approximation; not proprietary JdK RRG.

RS = sector/Nifty 500; strength = 100 + 14-week population z-score(RS).
Momentum = 100 + 14-week population z-score(one-week strength return).
Missing observations break rolling windows; no forward filling.
"""
import csv
import hashlib
import io
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
SOURCE = 'https://nsearchives.nseindia.com/content/indices/ind_close_all_{}.csv'
BENCHMARK = 'Nifty 500'
SECTORS = ('Nifty Auto','Nifty Bank','Nifty Financial Services','Nifty FMCG',
           'Nifty IT','Nifty Media','Nifty Metal','Nifty Pharma','Nifty PSU Bank',
           'Nifty Realty','Nifty Private Bank','Nifty Oil & Gas',
           'Nifty Healthcare Index','Nifty Consumer Durables','Nifty Chemicals',
           'Nifty Capital Markets','Nifty NBFC','Nifty Cement','Nifty Construction',
           'Nifty Hospitals','Nifty Insurance','Nifty Power','Nifty Retail',
           'Nifty Consumer Services','Nifty Capital Goods','Nifty Telecommunications')
CORE = {'Nifty Auto','Nifty Bank','Nifty FMCG','Nifty IT','Nifty Metal','Nifty Pharma'}


def completed_friday(now):
    now = now.astimezone(IST)
    friday = now.date() - timedelta(days=(now.weekday()-4) % 7)
    if now.weekday() == 4 and now.hour < 18:
        friday -= timedelta(days=7)
    return friday


def parse_csv(raw, expected):
    values = {}
    rows = list(csv.DictReader(io.StringIO(raw.lstrip('\ufeff'))))
    for row in rows:
        name = row.get('Index Name','').strip()
        if name not in set(SECTORS) | {BENCHMARK}:
            continue
        if datetime.strptime(row['Index Date'].strip(), '%d-%m-%Y').date() != expected:
            raise ValueError('Source date mismatch')
        close = float(row['Closing Index Value'])
        if not math.isfinite(close) or close <= 0:
            raise ValueError('Invalid index close')
        # Some official historical indices publish a close without intraday OHLC.
        # Only the close is used; check the range whenever NSE supplies it.
        low_raw, high_raw = row['Low Index Value'].strip(), row['High Index Value'].strip()
        if low_raw not in ('','-') and high_raw not in ('','-'):
            low, high = float(low_raw), float(high_raw)
            if not all(math.isfinite(x) and x > 0 for x in (low, high)) or not low <= close <= high:
                raise ValueError('Invalid index OHLC')
        if name in values and values[name] != close:
            raise ValueError('Conflicting duplicate index')
        values[name] = close
    if BENCHMARK not in values:
        raise ValueError('Nifty 500 benchmark missing')
    return values


def fetch_day(day, cache):
    path = cache / (day.isoformat()+'.csv')
    ttl = 21600 if (datetime.now(IST).date()-day).days < 14 else 604800
    if path.exists() and time.time()-path.stat().st_mtime < ttl:
        return parse_csv(path.read_text(), day)
    request = urllib.request.Request(SOURCE.format(day.strftime('%d%m%Y')),
                                    headers={'User-Agent':'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                raw = response.read().decode('utf-8-sig')
            values = parse_csv(raw, day)
            cache.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
            return values
        except urllib.error.HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt+1))


def fetch_week(friday, cache, fetch=fetch_day):
    for offset in range(5):
        day = friday - timedelta(days=offset)
        try:
            return {'week':friday.isoformat(),'date':day.isoformat(),'values':fetch(day,cache)}
        except urllib.error.HTTPError as error:
            if error.code not in (404,410):
                raise
    return {'week':friday.isoformat(),'date':None,'values':{}}


def zscore(series, window=14):
    out = []
    for i, value in enumerate(series):
        sample = series[max(0,i-window+1):i+1]
        if len(sample) != window or any(v is None for v in sample):
            out.append(None)
            continue
        std = statistics.pstdev(sample)
        out.append(None if std <= 1e-12 else 100+(value-statistics.mean(sample))/std)
    return out


def coordinates(rs):
    strength = zscore(rs)
    returns = [None] + [None if a is None or b is None or a == 0 else b/a-1
                        for a,b in zip(strength,strength[1:])]
    return list(zip(strength,zscore(returns)))


def quadrant(point):
    x,y = point
    if x is None or y is None:
        return 'Unavailable'
    if x == 100 or y == 100:
        return 'Boundary'
    return ('Leading' if y > 100 else 'Weakening') if x > 100 else ('Improving' if y > 100 else 'Lagging')


def report(weeks):
    results = {}
    for name in SECTORS:
        rs = [w['values'][name]/w['values'][BENCHMARK]
              if name in w['values'] and BENCHMARK in w['values'] else None for w in weeks]
        points = coordinates(rs)
        results[name] = {'group':quadrant(points[-1]),'previous':quadrant(points[-2]),
                         'strength':points[-1][0],'momentum':points[-1][1]}
    if any(results[s]['group'] == 'Unavailable' for s in CORE):
        raise ValueError('Core sector history incomplete; no rotation alert sent')
    last = weeks[-1]
    lines = ['Darpan | SECTOR ROTATION',f"Week ending: {last['week']} | Close: {last['date']}",
             'Benchmark: NIFTY 500 | Weekly completed closes']
    for group,icon in [('Leading','🟢'),('Improving','🔵'),('Weakening','🟠'),('Lagging','🔴'),('Boundary','⚪')]:
        names = [n.removeprefix('Nifty ') for n,r in results.items() if r['group']==group]
        lines.append(icon+' '+group+': '+(', '.join(names) or 'None'))
    changes = [n.removeprefix('Nifty ')+': '+r['previous']+' → '+r['group']
               for n,r in results.items() if r['previous'] not in ('Unavailable','Boundary')
               and r['group'] not in ('Unavailable','Boundary') and r['previous'] != r['group']]
    lines.append('Weekly changes: '+('; '.join(changes) or 'None'))
    missing = [n.removeprefix('Nifty ') for n,r in results.items() if r['group']=='Unavailable']
    lines.append(f'Coverage: {len(SECTORS)-len(missing)}/{len(SECTORS)} selected sector indices')
    if missing:
        lines.append('Insufficient history: '+', '.join(missing))
    lines.extend(['Source: official NSE daily index archive.',
                  'Open-source RS/momentum approximation; not licensed JdK RRG.',
                  'Sector watchlist only. Institutional evidence and HTF/entry alerts remain separate. This is not a buy signal.'])
    return '\n'.join(lines), results


def deliver(store, text, identity, destination, send):
    state = store.state
    if state.get('destination') not in (None,destination):
        raise ValueError('Destination changed; queued delivery held')
    state['destination'] = destination
    if identity not in state['delivered'] and not any(p['id']==identity for p in state['pending']):
        state['pending'].append({'id':identity,'text':text})
        store.save()
    sent = 0
    while state['pending']:
        item = state['pending'][0]
        response = send(item['text'])
        if not response.get('ok') or response.get('result',{}).get('chat',{}).get('type') != 'private':
            raise ValueError('Private Telegram delivery not confirmed')
        state['delivered'].append(item['id'])
        state['pending'].pop(0)
        store.save()
        sent += 1
    return sent


def main():
    friday = completed_friday(datetime.now(IST))
    dates = [friday-timedelta(weeks=i) for i in reversed(range(52))]
    cache = Path('.sector-rotation-cache')
    with ThreadPoolExecutor(max_workers=3) as pool:
        weeks = list(pool.map(lambda d:fetch_week(d,cache),dates))
    text, results = report(weeks)
    print(text)
    if '--dry-run' in sys.argv:
        return
    from institutional_confluence import Store, verify_private_destination, request
    destination = verify_private_destination()
    store = Store('sector-rotation-state.json')
    store.read()
    # Include the full input window so historical revisions are detectable.
    identity = hashlib.sha256(json.dumps(weeks,sort_keys=True).encode()).hexdigest()
    def send(message):
        return json.loads(request('https://api.telegram.org/bot'+os.environ['BOT_TOKEN'].strip()+'/sendMessage',
                                  {'chat_id':os.environ['CHAT_ID'].strip(),'text':message,'disable_web_page_preview':True}))
    sent = deliver(store,text,identity,destination,send)
    print(f'SECTOR ROTATION SUCCESS | Sent: {sent} | Waiting: {len(store.state["pending"])}')


if __name__ == '__main__':
    main()
