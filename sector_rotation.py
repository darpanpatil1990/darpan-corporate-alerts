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
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from decimal import Decimal
from urllib.parse import urljoin, urlparse
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


def constituent_members(raw):
    rows = list(csv.DictReader(io.StringIO(raw.lstrip('\ufeff'))))
    members = {}
    for row in rows:
        symbol, isin = row['Symbol'].strip(), row['ISIN Code'].strip()
        if symbol.startswith('DUMMY'):
            continue
        if row.get('Series','').strip() != 'EQ':
            continue
        if not re.fullmatch(r'[A-Z0-9&_.-]+',symbol) or not re.fullmatch(r'IN[A-Z0-9]{10}',isin):
            raise ValueError('Constituent identity invalid')
        if isin in members or symbol in members.values():
            raise ValueError('Duplicate constituent identity')
        members[isin] = symbol
    if not members:
        raise ValueError('No equity constituents found')
    return members


def eligible_stocks(sector_members, universe, caps):
    """Join by stock ISIN, never infer membership or cap from company names."""
    selected, missing, excluded = [], [], 0
    for isin in sorted(set(sector_members) & set(universe)):
        symbol = universe[isin]
        cap = caps.get(symbol)
        if not cap or cap.get('stock_isin') != isin:
            missing.append(symbol)
            continue
        amount = Decimal(str(cap['cap']))
        if not amount.is_finite() or amount <= 0:
            missing.append(symbol)
        elif amount > 500:
            selected.append(symbol)
        else:
            excluded += 1
    return {'stocks':sorted(selected),'cap_unavailable':sorted(missing),'cap_excluded':excluded}


def sector_page(name):
    slug = {'Nifty Healthcare Index':'nifty-healthcare-index',
            'Nifty Oil & Gas':'nifty-oil-and-gas-index',
            'Nifty Consumer Durables':'nifty-consumer-durables-index'}.get(name,name.lower().replace(' ','-'))
    category = 'thematic-indices' if name == 'Nifty Capital Markets' else 'sectoral-indices'
    return 'https://www.niftyindices.com/indices/equity/'+category+'/'+slug


def load_related(results):
    from bs4 import BeautifulSoup
    from scanner_runtime import read_bytes, load_scanx_caps, UNIVERSE_URL
    universe = constituent_members(read_bytes(UNIVERSE_URL).decode('utf-8-sig'))
    if not 450 <= len(universe) <= 550:
        raise ValueError('Nifty 500 constituent count invalid')
    caps, _ = load_scanx_caps({symbol:isin for isin,symbol in universe.items()})
    active = [name for name,r in results.items() if r['group'] != 'Unavailable']
    def fetch(name):
        page = sector_page(name)
        soup = BeautifulSoup(read_bytes(page),'html.parser')
        links = {urljoin(page,a['href']) for a in soup.select('a[href]')
                 if 'index constituent' in a.get_text(' ',strip=True).casefold()
                 and a['href'].lower().endswith('.csv')}
        if len(links) != 1:
            raise ValueError('Official sector constituent link ambiguous')
        link = links.pop()
        parsed = urlparse(link)
        normalized_path = '/'+parsed.path.lstrip('/')
        if parsed.hostname not in ('www.niftyindices.com','niftyindices.com') or not normalized_path.startswith('/IndexConstituent/'):
            raise ValueError('Unexpected constituent source')
        archive = 'https://nsearchives.nseindia.com/content/indices/'+parsed.path.rsplit('/',1)[-1]
        source = archive
        try:
            raw = read_bytes(archive)
        except urllib.error.HTTPError as error:
            if error.code not in (404,410):
                raise
            source = 'https://www.niftyindices.com'+normalized_path
            raw = read_bytes(source)
        members = constituent_members(raw.decode('utf-8-sig'))
        item = eligible_stocks(members,universe,caps)
        item['stock_isins'] = {symbol:isin for isin,symbol in universe.items() if symbol in item['stocks']}
        return name,dict(item,source=source)
    related, failures = {}, []
    def safe_fetch(name):
        try:
            return fetch(name)
        except (OSError,ValueError,KeyError) as error:
            print('RELATED SOURCE UNAVAILABLE | '+name+' | '+type(error).__name__,flush=True)
            return name,{'unavailable':type(error).__name__}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for name,value in pool.map(safe_fetch,active):
            related[name] = value
            if 'unavailable' in value:
                failures.append(name)
    print(f'RELATED STOCK COVERAGE | Sectors: {len(active)-len(failures)}/{len(active)} | Universe: {len(universe)} | Exact market caps: {len(caps)}')
    return related


def related_messages(results, related, week, checked):
    messages = []
    for group, icon in [('Leading','🟢'),('Improving','🔵'),('Weakening','🟠'),('Lagging','🔴'),('Boundary','⚪')]:
        names = [name for name,r in results.items() if r['group']==group]
        if not names:
            continue
        header = f'Darpan | SECTOR RELATED STOCKS\n{icon} Sector group: {group}\nRotation week: {week} | Constituents/caps checked: {checked}\nFilter: current NIFTY 500 equities (EQ); market cap strictly > ₹500 Cr.\n'
        footer = '\nAlphabetical constituent list, not stock RS ranking or a Buy signal. Stock HTF, achievement and institutional evidence remain separate. Source: official NSE membership; ScanX market caps.'
        parts, body = [], ''
        for name in names:
            item = related.get(name,{'unavailable':'missing'})
            label = name.removeprefix('Nifty ')
            if 'unavailable' in item:
                line = label+': constituent source unavailable; no stocks inferred.\n'
            else:
                line = label+f" ({len(item['stocks'])}): "+(', '.join(item['stocks']) or 'None eligible')
                if item['cap_unavailable']:
                    line += ' | Cap unavailable, omitted: '+', '.join(item['cap_unavailable'])
                if item['cap_excluded']:
                    line += f" | Cap ≤500 Cr excluded: {item['cap_excluded']}"
                line += '\n'
            if len(header+body+line+footer) > 3500:
                if not body or len(header+line+footer) > 3500:
                    raise ValueError('Sector stock list exceeds message budget')
                parts.append(body)
                body = ''
            body += line
        if body:
            parts.append(body)
        for i,part in enumerate(parts):
            # Check timestamps and harmless cap ticks do not trigger duplicates.
            identity = hashlib.sha256(json.dumps(['related-stocks-v1',week,group,i,part],ensure_ascii=False).encode()).hexdigest()
            messages.append((identity,header+part+footer))
    return messages


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
    related = load_related(results)
    stock_messages = related_messages(results,related,friday.isoformat(),datetime.now(IST).isoformat(timespec='minutes'))
    for _,message in stock_messages:
        print(message)
    if '--dry-run' in sys.argv:
        return
    from institutional_confluence import Store, verify_private_destination, request
    destination = verify_private_destination()
    store = Store('sector-rotation-state.json')
    store.read()
    store.state['rotation_snapshot'] = {'version':1,'week':friday.isoformat(),
        'close_date':weeks[-1]['date'],'generated_at':datetime.now(IST).isoformat(),
        'results':results,'related':related}
    store.save()
    # Include the full input window so historical revisions are detectable.
    identity = hashlib.sha256(json.dumps(weeks,sort_keys=True).encode()).hexdigest()
    def send(message):
        return json.loads(request('https://api.telegram.org/bot'+os.environ['BOT_TOKEN'].strip()+'/sendMessage',
                                  {'chat_id':os.environ['CHAT_ID'].strip(),'text':message,'disable_web_page_preview':True}))
    sent = deliver(store,text,identity,destination,send)
    for stock_identity,message in stock_messages:
        sent += deliver(store,message,stock_identity,destination,send)
    print(f'SECTOR ROTATION SUCCESS | Sent: {sent} | Waiting: {len(store.state["pending"])}')


if __name__ == '__main__':
    main()
