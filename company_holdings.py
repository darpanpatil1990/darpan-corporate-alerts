"""Dated company-level ownership changes; never equate holdings with trades."""
import base64
import csv
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

UNIVERSE_URL = 'https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv'
STATE_PATH = 'company-holdings-state.json'


def request(address, payload=None, token=None, method=None):
    headers = {'User-Agent': 'Darpan-Company-Holdings/1.0', 'Accept': 'application/json,text/html,*/*'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(address, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers=headers, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.read().decode('utf-8-sig')
        except urllib.error.HTTPError as error:
            # Retry only transient reads, never writes or Telegram delivery.
            if payload is not None or req.get_method() != 'GET' or attempt == 2 or error.code not in (429, 500, 502, 503, 504):
                raise
            delay = error.headers.get('Retry-After', '8')
            if not delay.isdigit() or int(delay) > 30:
                raise
            time.sleep(max(1, int(delay)))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if payload is not None or req.get_method() != 'GET' or attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


def read_company(symbol):
    address = 'https://www.screener.in/company/' + urllib.parse.quote(symbol, safe='') + '/'
    try:
        html = request(address)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        address += 'consolidated/'
        html = request(address)
    record = parse_company(html, symbol)
    if record:
        record['source'] = address + '#shareholding'
    return record


def universe_from_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    symbols = [r.get('Symbol', '').strip() for r in rows]
    if not 450 <= len(symbols) <= 550 or len(set(symbols)) != len(symbols):
        raise ValueError('Nifty 500 constituent count/uniqueness invalid')
    if any(not re.fullmatch(r'[A-Z0-9&_.-]+', s) for s in symbols):
        raise ValueError('Invalid constituent symbol')
    return symbols


def percentage(cell):
    value = cell.get_text(' ', strip=True)
    if not re.fullmatch(r'\d+(?:\.\d+)?%', value):
        raise ValueError('Missing or invalid holding percentage')
    number = Decimal(value[:-1])
    if not 0 <= number <= 100:
        raise ValueError('Holding outside 0-100%')
    return number


def parse_company(html, symbol, today=None):
    today = today or datetime.now(timezone.utc).date()
    soup = BeautifulSoup(html, 'html.parser')
    names = [re.sub(r'\s+', '', a.get_text(' ', strip=True)) for a in soup.select('a[href]')]
    if 'NSE:' + symbol not in names:
        raise ValueError('Company symbol does not match requested constituent')
    title = soup.find('h1')
    ratios = soup.select_one('#top-ratios')
    if title is None or ratios is None:
        raise ValueError('Company name or market cap missing')
    cap = None
    for item in ratios.select('li'):
        text = item.get_text(' ', strip=True)
        match = re.fullmatch(r'Market Cap\s*₹\s*([\d,.]+)\s*Cr\.', text)
        if match:
            cap = Decimal(match[1].replace(',', ''))
    if cap is None:
        raise ValueError('Market cap units/layout not recognised')
    if cap <= 500:
        return None
    table = soup.select_one('#quarterly-shp table')
    if table is None:
        raise ValueError('Quarterly shareholding table unavailable')
    headers = table.select('thead tr th')
    dated = []
    for i, cell in enumerate(headers):
        label = cell.get_text(' ', strip=True)
        if i == 0:
            continue
        dt = datetime.strptime(label, '%b %Y').date()
        if dt.month not in (3, 6, 9, 12):
            # Sources can append an interim month after the last completed quarter.
            # Do not relabel that observation as quarterly or discard valid quarters.
            continue
        dated.append((dt, i, label))
    if len(dated) < 2 or len({d[0] for d in dated}) != len(dated):
        raise ValueError('Comparable shareholding periods missing')
    dated.sort()
    previous, latest = dated[-2:]
    if (latest[0].year - previous[0].year) * 12 + latest[0].month - previous[0].month != 3:
        raise ValueError('Holding periods are not consecutive quarters')
    if not 0 <= (today - latest[0]).days <= 200:
        raise ValueError('Holding period stale or future dated')
    holdings = {}
    for row in table.select('tbody > tr'):
        cells = row.find_all(['td', 'th'], recursive=False)
        if not cells:
            continue
        label = re.sub(r'\s*\+\s*$', '', cells[0].get_text(' ', strip=True)).strip()
        if label not in ('FIIs', 'DIIs', 'Mutual Funds'):
            continue
        if len(cells) != len(headers):
            raise ValueError('Holding headers do not align with values')
        old, new = percentage(cells[previous[1]]), percentage(cells[latest[1]])
        holdings[label] = {'previous': str(old), 'latest': str(new), 'change_pp': str(new - old)}
    if not {'FIIs', 'DIIs'}.issubset(holdings):
        raise ValueError('FII/DII holding categories missing')
    return {'symbol': symbol, 'company': title.get_text(' ', strip=True), 'market_cap_cr': str(cap),
            'previous_period': previous[2], 'period': latest[2], 'holdings': holdings,
            'source': 'https://www.screener.in/company/' + urllib.parse.quote(symbol, safe='') + '/#shareholding',
            'checked_at': datetime.now(timezone.utc).isoformat()}


def signal_id(record):
    # Identity includes the reporting quarter and figures, never the check time or market price.
    content = [record['symbol'], record['previous_period'], record['period'], record['holdings']]
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def rising(record):
    return any(Decimal(r['change_pp']) > 0 for r in record['holdings'].values())


def message(record, initial=False):
    lines = ['Company ownership — ' + ('initial quarterly snapshot' if initial else 'newly observed quarterly figures'),
             record['company'] + ' (' + record['symbol'] + ')',
             'Nifty 500 | Market cap INR ' + record['market_cap_cr'] + ' crore',
             record['previous_period'] + ' → ' + record['period']]
    for category, values in record['holdings'].items():
        lines.append(f"{category}: {values['previous']}% → {values['latest']}% ({Decimal(values['change_pp']):+.2f} percentage points)")
    lines.extend(['', 'Quarter-end ownership change; not a confirmed purchase today.',
                  'DII includes institutions other than mutual funds. No fund/buyer identity inferred.',
                  'Demand-zone confluence and monthly MF portfolio buys are not checked here.',
                  'Source: Screener', record['source']])
    return '\n'.join(lines)


class Store:
    def __init__(self):
        self.token = os.environ['GH_TOKEN']
        self.branch = os.environ['GITHUB_REF_NAME']
        self.endpoint = 'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + '/contents/' + STATE_PATH
        self.sha = None
        self.state = {'version': 1, 'cursor': 0, 'records': {}, 'pending': [], 'delivered': []}

    def read(self):
        try:
            obj = json.loads(request(self.endpoint + '?ref=' + urllib.parse.quote(self.branch, safe=''), token=self.token))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return
            raise
        self.sha = obj['sha']
        self.state = json.loads(base64.b64decode(obj['content']))
        if self.state.get('version') != 1 or not isinstance(self.state.get('records'), dict) or not isinstance(self.state.get('pending'), list) or not isinstance(self.state.get('delivered'), list):
            raise ValueError('Invalid saved state; refusing to reset delivery history')

    def save(self):
        data = {'branch': self.branch, 'message': 'Update company ownership scan and delivery state',
                'content': base64.b64encode(json.dumps(self.state, separators=(',', ':')).encode()).decode()}
        if self.sha:
            data['sha'] = self.sha
        result = json.loads(request(self.endpoint, data, self.token, 'PUT'))
        self.sha = result['content']['sha']


def main():
    dry_run = '--dry-run' in __import__('sys').argv
    raw_universe = request(UNIVERSE_URL)
    all_symbols = universe_from_csv(raw_universe)
    rows = list(csv.DictReader(io.StringIO(raw_universe)))
    omitted = {r['Symbol'].strip(): 'Non-equity series; quarterly equity ownership source not supported'
               for r in rows if r.get('Series') and r['Series'].strip() != 'EQ'}
    symbols = [s for s in all_symbols if not s.startswith('DUMMY') and s not in omitted]
    limit = min(50, max(1, int(os.environ.get('HOLDINGS_BATCH_SIZE', '25'))))
    if dry_run:
        state = {'cursor': 0, 'records': {}, 'pending': [], 'delivered': []}
        store = None
    else:
        if not all(os.environ.get(k) for k in ('GH_TOKEN', 'BOT_TOKEN', 'CHAT_ID', 'GITHUB_REPOSITORY', 'GITHUB_REF_NAME')):
            raise ValueError('Required configuration missing')
        store = Store()
        store.read()
        state = store.state
    cursor = state['cursor'] % len(symbols)
    retries = [s for s in state.get('retry_symbols', state.get('last_batch', {}).get('failed', [])) if s in symbols]
    batch = list(dict.fromkeys(retries))[:limit // 2]
    consumed = 0
    while len(batch) < limit:
        symbol = symbols[(cursor + consumed) % len(symbols)]
        consumed += 1
        if symbol not in batch:
            batch.append(symbol)
    failed, checked, matches = [], [], []
    queued = {p['id'] for p in state['pending']}
    delivered = set(state['delivered'])
    for symbol in batch:
        try:
            record = read_company(symbol)
            checked.append(symbol)
            if record:
                initial = symbol not in state['records']
                state['records'][symbol] = record
                identity = signal_id(record)
                if rising(record):
                    matches.append(record)
                    if identity not in delivered and identity not in queued:
                        state['pending'].append({'id': identity, 'text': message(record, initial)})
                        queued.add(identity)
            else:
                state['records'].pop(symbol, None)
        except Exception as error:
            failed.append(symbol)
            detail = ' HTTP ' + str(error.code) if isinstance(error, urllib.error.HTTPError) else ''
            print('UNAVAILABLE:', symbol, type(error).__name__ + detail, flush=True)
        time.sleep(3)
    state['cursor'] = (cursor + consumed) % len(symbols)
    state['retry_symbols'] = list(dict.fromkeys([s for s in retries if s not in batch] + failed))
    state['universe_checked_at'] = datetime.now(timezone.utc).isoformat()
    state['universe_source'] = UNIVERSE_URL
    state['scope_exclusions'] = omitted
    state['last_batch'] = {'checked': checked, 'failed': failed, 'matching': [r['symbol'] for r in matches]}
    sent = 0
    if store:
        store.save()  # Persist the queue before contacting Telegram.
        while state['pending'] and sent < 25:
            item = state['pending'][0]
            result = json.loads(request('https://api.telegram.org/bot' + os.environ['BOT_TOKEN'].strip() + '/sendMessage',
                                        {'chat_id': os.environ['CHAT_ID'].strip(), 'text': item['text'], 'link_preview_options': {'is_disabled': True}}))
            if not result.get('ok'):
                raise ValueError('Telegram delivery rejected')
            state['delivered'].append(item['id'])
            state['pending'].pop(0)
            store.save()
            sent += 1
            time.sleep(1.2)
    Path('company-holdings-result.json').write_text(json.dumps({'universe_size': len(symbols), 'checked': checked, 'failed': failed, 'matches': matches}, indent=2))
    summary = f'Company ownership: checked {len(checked)}/{len(batch)} in this batch of {len(symbols)} constituents; rising {len(matches)}; sent {sent}; waiting {len(state["pending"])}; unavailable {len(failed)}.'
    print(summary, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## Company-level quarterly ownership\n\n' + summary + '\n\n')
            f.write('Screener source; Nifty 500 and market cap > INR 500 crore. Rolling batches; this is not a full-universe result per run. Failed symbols are retried on the next sweep.\n\n')
            f.write('Quarterly ownership changes are not daily trades. DII is not synonymous with mutual funds. Monthly MF portfolio buys and demand zones remain separate.\n\n')
            for symbol,reason in omitted.items():f.write('- OUTSIDE EQUITY OWNERSHIP SCOPE '+symbol+': '+reason+'\n')
            for record in matches:
                f.write('- ' + record['symbol'] + ' | ' + record['previous_period'] + ' → ' + record['period'] + ' | ' + record['source'] + '\n')
    if failed:
        raise SystemExit('Some companies unavailable; partial results and delivery queue retained')


if __name__ == '__main__':
    main()

