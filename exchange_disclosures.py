"""NSE acquisition evidence: transaction dates, exact stock identities and open-market buys."""
import hashlib,json,re
import urllib.parse as url
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta
from decimal import Decimal

ROOT='https://www.nseindia.com'
PIT='/api/corporates-pit-gg?index='
SAST='/api/corporate-sast-reg29?'
ARCHIVES={'nsearchives.nseindia.com','archives.nseindia.com'}
FIELDS={
 'buyer':'NameOfThePerson','instrument':'TypeOfInstrument','category':'CategoryOfPerson',
 'action':'SecuritiesAcquiredOrDisposedTransactionType','qty':'SecuritiesAcquiredOrDisposedNumberOfSecurity',
 'start':'DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate',
 'end':'DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate',
 'mode':'ModeOfAcquisitionOrDisposal','venue':'ExchangeOnWhichTheTradeWasExecuted'}

def clean(value):return re.sub(r'\s+',' ',str(value or '')).strip()
def date(value):
    for fmt in ('%Y-%m-%d','%d-%b-%Y'):
        try:return datetime.strptime(clean(value),fmt).date()
        except ValueError:pass
    raise ValueError('NSE transaction date unavailable: '+clean(value)[:50])
def report_date(value):
    for fmt in ('%d-%b-%Y %H:%M:%S','%d-%b-%Y %H:%M'):
        try:return datetime.strptime(clean(value),fmt).date()
        except ValueError:pass
    raise ValueError('NSE publication date unavailable')
def quantity(value):
    value=Decimal(clean(value).replace(',',''))
    if not value.is_finite() or value<=0 or value!=value.to_integral_value():raise ValueError('NSE acquired share quantity invalid')
    return str(int(value))
def archive_link(value,xml=False):
    p=url.urlparse(value or '')
    if p.scheme!='https' or p.hostname not in ARCHIVES or not p.path.startswith('/corporate/') or p.query or p.fragment or xml and not p.path.endswith('.xml'):
        raise ValueError('NSE attachment source invalid')
    return value
def period(start,end,published):
    a,b=date(start),date(end)
    if a>b or b>published:raise ValueError('NSE transaction period contradicts publication date')
    return a.isoformat(),b.isoformat()

def parse_pit(blob,filing,isin):
    if len(blob)>2_000_000 or b'<!DOCTYPE' in blob.upper() or b'<!ENTITY' in blob.upper():raise ValueError('Unsupported NSE XBRL document')
    archive_link(filing['xmlFileName'],True)
    groups={}
    wanted=set(FIELDS.values())|{'Symbol','ISINCode'}
    for e in ET.fromstring(blob).iter():
        name=e.tag.rsplit('}',1)[-1];context=e.get('contextRef')
        if context and name in wanted:
            facts=groups.setdefault(context,{});value=clean(e.text)
            if name in facts and facts[name]!=value:raise ValueError('Conflicting NSE XBRL facts')
            facts[name]=value
    symbols={f['Symbol'] for f in groups.values() if f.get('Symbol')};identities={f['ISINCode'] for f in groups.values() if f.get('ISINCode')}
    if symbols!={filing['symbol']} or identities!={isin}:raise ValueError('NSE XBRL stock identity mismatch')
    published=report_date(filing['broadcastDateTime']);events=[];excluded=Counter();contexts=0
    for context,facts in groups.items():
        if FIELDS['instrument'] not in facts:continue
        contexts+=1;f={k:facts.get(v,'') for k,v in FIELDS.items()}
        if not f['instrument']:raise ValueError('NSE instrument type unavailable')
        if f['instrument'].lower() not in ('equity','equity shares','units of reit','units of invit'):
            excluded['Not equity/REIT/InvIT units']+=1;continue
        if not f['action']:raise ValueError('NSE equity transaction action unavailable')
        if f['action'].lower() not in ('buy','purchase','acquisition'):
            excluded['Not an acquisition']+=1;continue
        if f['mode'].lower() not in ('market purchase','open market','open market purchase'):
            excluded['Not a disclosed open-market purchase']+=1;continue
        start,end=period(f['start'],f['end'],published);qty=quantity(f['qty'])
        if not f['buyer']:raise ValueError('NSE XBRL acquirer missing')
        events.append({'kind':'Insider disclosed acquisition','stock_isin':isin,'buyer':f['buyer'],'buyer_category':f['category'],
         'start':start,'end':end,'qty':qty,'mode':f['mode'],'execution_venue':f['venue'] or 'Not supplied',
         'date_basis':'NSE transaction period','reported_at':filing['broadcastDateTime'],
         'filing_id':str(filing['appId'])+':'+context,'source':filing['xmlFileName'],'submission_type':filing.get('typeOfSubmission','Original'),
         'caveat':'Completed open-market acquisition as disclosed in NSE XBRL. The transaction period is separate from its publication date; insider status does not establish an investment thesis.'})
    if not contexts:raise ValueError('NSE PIT transaction schema unavailable')
    return {'symbol':filing['symbol'],'isin':isin,'events':events,'contexts':contexts,'excluded':dict(excluded)}

def parse_sast(rows,members,today,aliases=None):
    aliases=aliases or {};events=[];excluded=Counter();errors=[];selected=0
    for row in rows:
        symbol=aliases.get(row.get('symbol'),row.get('symbol'))
        if symbol not in members:continue
        selected+=1
        try:
            if clean(row['acqSaleType']).lower()!='acquisition':excluded['Not an acquisition']+=1;continue
            if clean(row['acquisitionMode']).lower()!='open market':excluded['Not a disclosed open-market purchase']+=1;continue
            if clean(row['acqType']).lower()!='equity shares':excluded['Not equity shares']+=1;continue
            dates=re.split(r'\s+to\s+',clean(row['acquirerDate']),flags=re.I)
            if len(dates) not in (1,2):raise ValueError('NSE SAST transaction date range invalid')
            published=report_date(row['sysTime']);start,end=period(dates[0],dates[-1],published)
            qty=quantity(row['noOfShareAcq']);source=archive_link(row['attachement'])
            if not clean(row['acquirerName']):raise ValueError('NSE SAST acquirer missing')
            if not 0<=(today-date(end)).days<=45:excluded['Outside transaction lookback']+=1;continue
            events.append((symbol,{'kind':'SAST disclosed acquisition','stock_isin':members[symbol],'buyer':clean(row['acquirerName']),
             'buyer_category':'Promoter' if row.get('promoterType')=='Y' else 'Not marked as promoter',
             'start':start,'end':end,'qty':qty,'mode':'Open Market','execution_venue':'Not supplied on SAST feed',
             'date_basis':'NSE transaction period','reported_at':row['sysTime'],'filing_id':str(row['application_no']),'source':source,
             'caveat':'NSE SAST open-market acquisition with disclosed transaction dates. Gift, inter-se transfer and allotment records are excluded; promoter status is used only as explicitly disclosed.'}))
        except Exception as error:errors.append(symbol+': '+type(error).__name__+' '+str(error)[:100])
    return events,{'selected_rows':selected,'qualifying_rows':len(events),'excluded':dict(excluded),'errors':errors}

def fetch_feed(target,fetch,start,end,published_field):
    result=json.loads(fetch(target))
    if not isinstance(result,dict) or not isinstance(result.get('data'),list):raise ValueError('NSE acquisition feed schema changed')
    rows=result['data']
    if not rows:raise ValueError('NSE acquisition window unexpectedly empty')
    if any(not start<=report_date(r[published_field])<=end for r in rows):raise ValueError('NSE feed ignored the requested publication window')
    return rows

def load(kind,members,today,fetch,cached,aliases=None):
    start=today-timedelta(days=45);query='&from_date='+start.strftime('%d-%m-%Y')+'&to_date='+today.strftime('%d-%m-%Y')
    if kind=='SAST':
        target=ROOT+SAST+query.lstrip('&');rows=fetch_feed(target,fetch,start,today,'sysTime')
        events,status=parse_sast(rows,members,today,aliases)
        status.update({'source':target,'feed_rows':len(rows),'complete':not status['errors'],'date_basis':'NSE transaction dates, separate from publication date','start':start.isoformat(),'end':today.isoformat()})
        return events,status
    if kind!='Insider':raise ValueError('Unsupported NSE acquisition kind')
    aliases=aliases or {};filings=[];sources=[]
    # NSE exposes listed REIT/InvIT filings separately from the equity list.
    for segment in ('equities','invitsreits'):
        target=ROOT+PIT+segment+query;sources.append(target)
        if segment=='invitsreits':
            result=json.loads(fetch(target))
            if not isinstance(result,dict) or not isinstance(result.get('data'),list):raise ValueError('NSE REIT PIT schema changed')
            rows=result['data']
            if any(not start<=report_date(r['broadcastDateTime'])<=today for r in rows):raise ValueError('NSE REIT feed publication window mismatch')
        else:rows=fetch_feed(target,fetch,start,today,'broadcastDateTime')
        filings.extend(rows)
    superseded={str(f['prevAppId']) for f in filings if f.get('prevAppId')}
    selected={}
    for f in filings:
        symbol=aliases.get(f.get('symbol'),f.get('symbol'))
        if symbol in members and str(f['appId']) not in superseded:
            key=archive_link(f['xmlFileName'],True)
            if key in selected and selected[key][0]!=symbol:raise ValueError('NSE filing mapped to conflicting stock identities')
            selected[key]=(symbol,f)
    events=[];errors=[];contexts=0;excluded=Counter()
    def read(item):
        target,(symbol,filing)=item
        key=hashlib.sha256(target.encode()).hexdigest()
        parsed=cached('nse-pit',key,90*86400,lambda:parse_pit(fetch(target),filing,members[symbol]))
        if parsed['symbol']!=filing['symbol'] or parsed['isin']!=members[symbol]:raise ValueError('Cached NSE filing identity mismatch')
        return symbol,parsed
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks={pool.submit(read,item):item for item in selected.items()}
        for task in as_completed(tasks):
            try:
                symbol,parsed=task.result();contexts+=parsed['contexts'];excluded.update(parsed['excluded'])
                for e in parsed['events']:
                    if 0<=(today-date(e['end'])).days<=45:events.append((symbol,e))
                    else:excluded['Outside transaction lookback']+=1
            except Exception as error:errors.append(tasks[task][1][0]+': '+type(error).__name__+' '+str(error)[:130])
    return events,{'source':sources,'feed_rows':len(filings),'selected_filings':len(selected),'verified_filings':len(selected)-len(errors),
      'superseded_filings':len(superseded),'transaction_contexts':contexts,'qualifying_rows':len(events),'excluded':dict(excluded),'errors':errors,
      'complete':not errors,'date_basis':'NSE XBRL transaction periods; exact stock ISINs; publication timestamps separate',
      'start':start.isoformat(),'end':today.isoformat()}
