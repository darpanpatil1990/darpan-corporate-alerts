"""Verified NSE final cash-market OHLC, isolated from other exchanges."""
import csv,io,json,math,time,zipfile
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta,timezone
from pathlib import Path

IST=timezone(timedelta(hours=5,minutes=30))
ROOT_URL='https://nsearchives.nseindia.com/content/cm/'
SERIES={'EQ':0,'RR':1,'BE':2,'BZ':3}

def archive_url(date):return ROOT_URL+'BhavCopy_NSE_CM_0_0_0_'+date.strftime('%Y%m%d')+'_F_0000.csv.zip'

def parse_archive(blob,date,isin_map):
    wanted=set(isin_map.values());output={}
    with zipfile.ZipFile(io.BytesIO(blob)) as book:
        names=[n for n in book.namelist() if n.endswith('.csv')]
        expected='BhavCopy_NSE_CM_0_0_0_'+date.strftime('%Y%m%d')+'_F_0000.csv'
        if names!=[expected]:raise ValueError('NSE archive file/date mismatch')
        rows=csv.DictReader(io.StringIO(book.read(expected).decode('utf-8-sig')))
        required={'TradDt','Sgmt','Src','ISIN','TckrSymb','SctySrs','OpnPric','HghPric','LwPric','ClsPric','SsnId'}
        if not required.issubset(rows.fieldnames or []):raise ValueError('NSE final archive schema changed')
        for row in rows:
            if row['TradDt']!=date.isoformat() or row['Src']!='NSE' or row['Sgmt']!='CM':raise ValueError('NSE source/date mismatch')
            isin=row['ISIN'];series=row['SctySrs']
            if isin not in wanted or series not in SERIES or row['SsnId']!='F1':continue
            values=[float(row[k]) for k in ('OpnPric','HghPric','LwPric','ClsPric')]
            o,h,l,c=values
            if any(not math.isfinite(v) or v<=0 for v in values) or not l<=min(o,c)<=max(o,c)<=h:continue
            item={'date':date.isoformat(),'symbol':row['TckrSymb'],'isin':isin,'series':series,'ohlc':values,'source':archive_url(date)}
            old=output.get(isin)
            if old and SERIES[old['series']]==SERIES[series] and old!=item:raise ValueError('Conflicting NSE final rows')
            if not old or SERIES[series]<SERIES[old['series']]:output[isin]=item
    return output

def cached_archive(date,isin_map,fetch,cache):
    folder=Path(cache)/'nse-final';folder.mkdir(parents=True,exist_ok=True)
    file=folder/(date.isoformat()+'.zip');absent=folder/(date.isoformat()+'.missing')
    if file.exists():return parse_archive(file.read_bytes(),date,isin_map)
    if absent.exists() and 0<=time.time()-absent.stat().st_mtime<600:return None
    try:blob=fetch(archive_url(date))
    except Exception as error:
        if getattr(error,'code',None)==404:absent.touch();return None
        raise
    rows=parse_archive(blob,date,isin_map)
    temp=file.with_suffix('.tmp');temp.write_bytes(blob);temp.replace(file)
    return rows

def bootstrap(now,isin_map,fetch,cache,history_symbols=('BAGMANE',),history_days=200):
    start=now.date() if now.hour>=16 else now.date()-timedelta(days=1)
    latest=None;rows=None
    for n in range(10):
        date=start-timedelta(days=n)
        found=cached_archive(date,isin_map,fetch,cache)
        if found is not None:latest=date;rows=found;break
    if latest is None:raise ValueError('No recent NSE final archive available')
    result={k:[v] for k,v in rows.items()};errors=[];files=1
    targets={isin_map[s] for s in history_symbols if s in isin_map}
    if targets:
        mapping={s:i for s,i in isin_map.items() if i in targets}
        dates=[latest-timedelta(days=i) for i in range(1,history_days)]
        with ThreadPoolExecutor(max_workers=3) as executor:
            tasks={executor.submit(cached_archive,d,mapping,fetch,cache):d for d in dates}
            for task in as_completed(tasks):
                date=tasks[task]
                try:
                    older=task.result()
                    if older is None:continue
                    files+=1
                    for isin,item in older.items():result.setdefault(isin,[]).append(item)
                except Exception as error:errors.append({'date':date.isoformat(),'error':type(error).__name__+': '+str(error)[:100]})
    for items in result.values():items.sort(key=lambda x:x['date'])
    return result,{'source':archive_url(latest),'latest_final_date':latest.isoformat(),'verified_current_isins':len(rows),'archive_files':files,'history_errors':errors,'history_rows':{s:len(result.get(isin_map[s],[])) for s in history_symbols if s in isin_map}}

def augment(chart,isin,official):
    """Replace complete daily bars, never individual fields or BSE candles."""
    if isin not in official:return chart
    r=chart['chart']['result'][0];meta=r['meta']
    if not meta.get('symbol','').endswith('.NS') or meta.get('currency')!='INR' or meta.get('dataGranularity')!='1d':raise ValueError('NSE final rows cannot be mixed with a different exchange/currency/granularity')
    q=r['indicators']['quote'][0];bars={}
    for i,stamp in enumerate(r.get('timestamp',[])):
        date=datetime.fromtimestamp(stamp,IST).date().isoformat()
        bars[date]=(stamp,[q[k][i] for k in ('open','high','low','close')])
    sources={}
    for item in official[isin]:
        if item['isin']!=isin:raise ValueError('NSE stock ISIN mismatch')
        date=item['date'];stamp=int(datetime.fromisoformat(date).replace(hour=9,minute=15,tzinfo=IST).timestamp())
        bars[date]=(stamp,item['ohlc']);sources[date]=item['source']
    keys=sorted(bars);quote={k:[bars[d][1][i] for d in keys] for i,k in enumerate(('open','high','low','close'))}
    return {'chart':{'result':[{'meta':dict(meta),'timestamp':[bars[d][0] for d in keys],'indicators':{'quote':[quote]},'events':r.get('events',{})}]},'_official_final_dates':sorted(sources),'_official_close_sources':sources}
