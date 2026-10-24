import copy,json,unittest
from datetime import datetime,timedelta
from unittest.mock import patch,Mock
import scanner_runtime as s

class RuntimeTests(unittest.TestCase):
 def mf(self):
  return {'code':0,'headers':['amcid','sch','planid','isin','shares','assetPerc','onemonthSharesCh','onemonthCh','threemonthCh','date','seo'],'data':[['1','Fund','22','INF000000001','120|120|100|100','2|2|5|5',20,-3,0,'2026-09|2026-09|2026-08|2026-08','fund']]}
 def test_quantity_not_weight_and_identical_month_duplicates(self):
  items,ids=s.mf_page_items(self.mf(),'INE000000001',s.day('2026-10-23'))
  self.assertEqual(items[0]['change'],20);self.assertEqual(items[0]['end'],'2026-09-30');self.assertEqual(ids,{'22:INF000000001'})
 def test_zero_provider_delta_with_repeated_months_is_recomputed(self):
  d=self.mf();d['data'][0][6]=0
  items,_=s.mf_page_items(d,'INE000000001',s.day('2026-10-23'))
  self.assertEqual(items[0]['change'],20)
 def test_distinct_scheme_options_are_not_combined(self):
  d=self.mf();r=copy.deepcopy(d['data'][0]);r[3]='INF000000002';d['data'].append(r)
  items,ids=s.mf_page_items(d,'INE000000001',s.day('2026-10-23'))
  self.assertEqual(len(ids),2);self.assertEqual(len(items),2)
 def test_conflicting_duplicate_rejected(self):
  d=self.mf();d['data'][0][4]='120|119|100|100'
  with self.assertRaises(ValueError):s.mf_page_items(d,'INE000000001',s.day('2026-10-23'))
 def test_nonconsecutive_and_unreconciled_rejected(self):
  for field,value in [(6,21),(9,'2026-09|2026-09|2026-07|2026-07')]:
   d=self.mf();d['data'][0][field]=value
   with self.assertRaises(ValueError):s.mf_page_items(d,'INE000000001',s.day('2026-10-23'))
 def chart(self,now):
  times=[int((now-timedelta(days=80-i)).replace(hour=9,minute=15).timestamp()) for i in range(81)]
  return {'chart':{'result':[{'meta':{'symbol':'TEST.NS','currency':'INR','dataGranularity':'1d','regularMarketTime':int(now.timestamp())-60,'regularMarketPrice':102,'currentTradingPeriod':{'regular':{'start':int(now.replace(hour=9,minute=15).timestamp()),'end':int(now.replace(hour=15,minute=30).timestamp())}}},'timestamp':times,'indicators':{'quote':[{'open':[100]*81,'high':[103]*81,'low':[99]*81,'close':[101]*81}]}}]}}
 def test_live_daily_bar_not_completed(self):
  now=datetime(2026,10,23,12,tzinfo=s.IST);payload,_,quality=s.price_payload(self.chart(now),'TEST',now)
  self.assertFalse(payload['rows'][-1][5]);self.assertEqual(payload['quote']['price'],102);self.assertEqual(quality['completed_asof'],'2026-10-22')
 def test_stale_live_quote_uses_completed_close(self):
  now=datetime(2026,10,23,12,tzinfo=s.IST);d=self.chart(now);d['chart']['result'][0]['meta']['regularMarketTime']-=3600
  payload,_,_=s.price_payload(d,'TEST',now);self.assertEqual(payload['quote']['asof'],'2026-10-22');self.assertEqual(payload['quote']['price'],101)
 def test_weekend_uses_friday(self):
  friday=datetime(2026,10,23,17,tzinfo=s.IST);payload,_,_=s.price_payload(self.chart(friday),'TEST',friday+timedelta(days=2))
  self.assertEqual(payload['quote']['asof'],'2026-10-23')
 def test_real_invalid_and_float_rounding(self):
  now=datetime(2026,10,23,17,tzinfo=s.IST);d=self.chart(now);q=d['chart']['result'][0]['indicators']['quote'][0];q['high'][1]=100.999999;q['low'][2]=110
  _,_,quality=s.price_payload(d,'TEST',now);self.assertEqual(len(quality['rounding_repairs']),1);self.assertEqual(len(quality['invalid_rows']),1)
 def test_non_daily_rejected(self):
  now=datetime(2026,10,23,17,tzinfo=s.IST);d=self.chart(now);d['chart']['result'][0]['meta']['dataGranularity']='1mo'
  with self.assertRaises(ValueError):s.price_payload(d,'TEST',now)
 def test_pagination_only_observed_same_path(self):
  self.assertEqual(s.following_page('<a rel="next" href="?p=2">Next</a>','https://www.screener.in/trades/bulk/'),'https://www.screener.in/trades/bulk/?p=2')
  self.assertIsNone(s.following_page('<a rel="next" href="https://elsewhere.test/">Next</a>','https://www.screener.in/trades/bulk/'))
 def test_wrong_destination_stops_delivery(self):
  store=Mock();store.state={'pending':[{'id':'queued','text':'dated research match'}],'delivered':[]}
  with patch.object(s,'verify_private_destination',return_value='different'),patch.object(s,'request') as send:
   with self.assertRaises(ValueError):s.deliver_pending(store,'verified-private')
  send.assert_not_called();self.assertEqual(len(store.state['pending']),1)
 def test_rejected_delivery_keeps_queue(self):
  store=Mock();store.state={'pending':[{'id':'queued','text':'dated research match'}],'delivered':[]}
  with patch.object(s,'verify_private_destination',return_value='verified-private'),patch.object(s,'request',return_value='{"ok":false}'),patch.dict(s.os.environ,{'BOT_TOKEN':'unit-test-token','CHAT_ID':'unit-test-private'}):
   with self.assertRaises(ValueError):s.deliver_pending(store,'verified-private')
  self.assertEqual(store.state['delivered'],[]);self.assertEqual(len(store.state['pending']),1)
 def test_renamed_symbol_requires_same_isin(self):
  d={'code':0,'data':[{'Sym':'NEW','Isin':'INE000000001','Exch':'NSE','Mcap':700}]}
  with patch.object(s,'cached_json',return_value=d):values,status=s.load_scanx_caps({'OLD':'INE000000001'})
  self.assertEqual(values['OLD']['provider_symbol'],'NEW');self.assertEqual(status['symbol_aliases'],{'OLD':'NEW'})
 def test_official_dummy_placeholder_has_no_invented_price(self):
  with patch.object(s,'market_cap') as cap:
   value=s.scan_stock('DUMMYHEG','DUM545A01024',datetime(2026,10,24,tzinfo=s.IST))
  self.assertIn('placeholder',value['excluded']);cap.assert_not_called()
 def test_abbreviated_source_date_is_normalized_for_trade_parser(self):
  html='<main><table><thead><tr><th>Date</th></tr></thead><tbody><tr><td>Oct. 23, 2026</td></tr></tbody></table></main>'
  normalized,dates=s.disclosure_dates(html,normalize=True)
  self.assertEqual(dates,[s.day('2026-10-23')]);self.assertIn('2026-10-23',normalized)
 def test_ambiguous_relative_source_date_is_not_invented(self):
  html='<main><table><thead><tr><th>Date</th></tr></thead><tbody><tr><td>Yesterday</td></tr></tbody></table></main>'
  with self.assertRaises(ValueError):s.disclosure_dates(html)
 def test_absolute_nested_date_is_not_lost_in_display_label(self):
  cell=s.BeautifulSoup('<td>Yesterday <span>2026-10-23</span></td>','html.parser').td
  self.assertEqual(s.disclosure_day(cell),s.day('2026-10-23'))
 def test_relative_date_does_not_discard_other_dated_disclosures(self):
  html='<main><table><thead><tr><th>Company</th><th>Person</th><th>Date</th><th>Type</th><th>Value</th></tr></thead><tbody><tr><td><a href="/company/TEST/">TEST</a></td><td>Unresolved</td><td>Yesterday</td><td>Buy</td><td>10</td></tr><tr><td><a href="/company/TEST/">TEST</a></td><td>Verified</td><td>22 Oct 2026</td><td>Buy</td><td>20</td></tr></tbody></table></main>'
  with patch.object(s,'login',return_value=Mock()),patch.object(s,'disclosure_page',return_value=html):
   records,status=s.read_disclosures('Insider','/trades/insiders/', ['TEST'],s.day('2026-10-24'),s.time.monotonic()+60)
  self.assertEqual(len(records),1);self.assertEqual(records[0][1]['buyer'],'Verified')
  self.assertFalse(status['complete']);self.assertEqual(status['unresolved_date_rows'],1)
 def test_reporting_date_is_not_claimed_as_transaction_date(self):
  item=s.canonical_evidence({'kind':'Insider disclosed acquisition','buyer':'Reported buyer','start':'2026-10-23','end':'2026-10-23','source':'https://www.screener.in/trades/insiders/?p=2'})
  self.assertIn('reporting date',item['date_basis'])
  self.assertFalse(s.acquisition_overlaps({'latestTouch':'2026-10-22'},item,'2026-10-23',[]))
 def test_reporting_date_queue_is_held_and_history_is_preserved(self):
  evidence={'kind':'Insider disclosed acquisition','buyer':'Reported buyer','start':'2026-10-23','end':'2026-10-23','source':'https://www.screener.in/trades/insiders/'}
  zone={'tf':'Monthly','date':'2026-09-01','latestTouch':'2026-10-22'}
  old=s.match_identity('TEST',[(zone,[evidence])],False)
  state={'records':{'TEST':{'evidence':[{'zone':zone,'evidence':[evidence]}]}},'pending':[{'id':old,'text':'unsent reporting-only evidence'}],'delivered':['prior-delivery']}
  s.migrate_pagination_identities(state)
  self.assertEqual(state['pending'],[]);self.assertEqual(state['delivered'],['prior-delivery'])
  self.assertEqual(state['held_for_evidence_review'][0]['id'],old)
 def test_removing_reporting_evidence_does_not_replay_already_delivered_match(self):
  evidence={'kind':'Insider disclosed acquisition','buyer':'Reported buyer','start':'2026-10-23','end':'2026-10-23','source':'https://www.screener.in/trades/insiders/'}
  bulk=dict(evidence,kind='Bulk disclosed acquisition',source='https://www.screener.in/trades/bulk/')
  zone={'tf':'Monthly','date':'2026-09-01','latestTouch':'2026-10-22','d':90,'p':110,'firstReturn':True}
  old=s.match_identity('TEST',[(zone,[evidence,bulk])],False)
  stock={'symbol':'TEST','cmp':100,'quality':{'quote':{'kind':'Close','asof':'2026-10-23'}},'evidence':[{'zone':zone,'evidence':[evidence,bulk]}]}
  state={'records':{'TEST':stock},'pending':[{'id':old,'text':'old combined match'}],'delivered':[old]}
  s.migrate_pagination_identities(state)
  new=s.match_identity('TEST',[(zone,[bulk])])
  self.assertIn(old,state['delivered']);self.assertIn(new,state['delivered']);self.assertFalse(state['pending'])
 def test_later_page_failure_preserves_dated_acquisitions(self):
  html='<main><table><thead><tr><th>Company</th><th>Person</th><th>Date</th><th>Type</th><th>Value</th></tr></thead><tbody><tr><td><a href="/company/TEST/">TEST</a></td><td>Named acquirer</td><td>23 Oct 2026</td><td>Buy</td><td>10</td></tr></tbody></table><a rel="next" href="?p=2">Next</a></main>'
  with patch.object(s,'login',return_value=Mock()),patch.object(s,'disclosure_page',side_effect=[html,ValueError('Source unavailable')]),patch.object(s.time,'sleep'):
   records,status=s.read_disclosures('Bulk','/trades/bulk/', ['TEST'],s.day('2026-10-24'),s.time.monotonic()+60)
  self.assertEqual(len(records),1);self.assertFalse(status['complete']);self.assertIn('Source unavailable',status['scope'])
 def test_provider_rename_keeps_established_price_history(self):
  now=datetime(2026,10,23,17,tzinfo=s.IST)
  result=Mock(stdout=json.dumps([{'symbol':'TEST','cmp':101,'asof':'2026-10-23','zones':[]}]))
  with patch.object(s,'market_cap',return_value={'cap':'700','provider_symbol':'RENAMED'}),patch.object(s,'prices',return_value=(self.chart(now),'verified-source')) as prices,patch.object(s.subprocess,'run',return_value=result):
   value=s.scan_stock('TEST','INE000000001',now)
  prices.assert_called_once_with('TEST');self.assertEqual(value['quality']['completed_asof'],'2026-10-23')
 def test_exchange_series_cannot_be_mixed(self):
  now=datetime(2026,10,23,17,tzinfo=s.IST);d=self.chart(now)
  with self.assertRaises(ValueError):s.price_payload(d,'TEST',now,'BSE')
  d['chart']['result'][0]['meta']['symbol']='TEST.BO'
  payload,_,_=s.price_payload(d,'TEST',now,'BSE')
  self.assertEqual(payload['quote']['asof'],'2026-10-23')
 def test_page_moves_do_not_create_new_alerts(self):
  zone={'tf':'Monthly','date':'2026-08-01','latestTouch':'2026-09-01'}
  item={'kind':'Bulk disclosed acquisition','buyer':'Named buyer','start':'2026-10-23','end':'2026-10-23','qty':'100','source':'https://www.screener.in/trades/bulk/?p=2'}
  moved=dict(item,source='https://www.screener.in/trades/bulk/?p=3')
  self.assertEqual(s.match_identity('TEST',[(zone,[item])]),s.match_identity('TEST',[(zone,[moved])]))
  self.assertNotEqual(s.match_identity('TEST',[(zone,[item])]),s.match_identity('TEST',[(zone,[dict(item,qty='200')])]))
 def test_pagination_migration_preserves_delivery_history(self):
  zone={'tf':'Monthly','date':'2026-08-01','latestTouch':'2026-09-01'};item={'source':'https://www.screener.in/trades/bulk/?p=2','buyer':'Named buyer'}
  old=s.match_identity('TEST',[(zone,[item])],False);new=s.match_identity('TEST',[(zone,[item])])
  state={'records':{'TEST':{'evidence':[{'zone':zone,'evidence':[item]}]}},'delivered':[old],'pending':[{'id':old,'text':'queued'}]}
  s.migrate_pagination_identities(state)
  self.assertIn(old,state['delivered']);self.assertIn(new,state['delivered']);self.assertEqual(state['pending'],[])
 def test_missing_latest_close_cannot_trigger_current_price_alert(self):
  now=datetime(2026,10,23,17,tzinfo=s.IST);d=self.chart(now);d['chart']['result'][0]['indicators']['quote'][0]['close'][-1]=None
  _,_,quality=s.price_payload(d,'TEST',now)
  self.assertEqual(quality['completed_asof'],'2026-10-22');self.assertFalse(quality['current_alerts_eligible'])
 def test_relative_label_uses_absolute_source_attribute(self):
  cell=s.BeautifulSoup('<td><time datetime="2026-10-23">yesterday</time></td>','html.parser').td
  self.assertEqual(s.disclosure_day(cell),s.day('2026-10-23'))
 def test_conflicting_source_date_metadata_is_rejected(self):
  cell=s.BeautifulSoup('<td title="2026-10-22"><time datetime="2026-10-23">yesterday</time></td>','html.parser').td
  with self.assertRaises(ValueError):s.disclosure_day(cell)
 def test_quote_clock_is_read_after_data_arrives(self):
  current=datetime(2026,10,23,12,tzinfo=s.IST);chart=self.chart(current)
  result=Mock(stdout=json.dumps([{'symbol':'TEST','cmp':102,'asof':'2026-10-23','zones':[]}]))
  with patch.object(s,'datetime',wraps=datetime) as clock,patch.object(s,'market_cap',return_value={'cap':'700'}),patch.object(s.subprocess,'run',return_value=result):
   clock.now.return_value=current-timedelta(minutes=30)
   def fetch(symbol):
    clock.now.return_value=current
    return chart,'verified-source'
   with patch.object(s,'prices',side_effect=fetch):value=s.scan_stock('TEST','INE000000001')
  self.assertIn('Live session',value['quality']['quote']['kind'])
 def test_market_cap_requires_exact_stock_identity(self):
  d={'code':0,'data':[{'Sym':'TEST','Isin':'WRONG','Exch':'NSE','Mcap':99999},{'Sym':'RIGHT','Isin':'INE000000002','Exch':'NSE','Mcap':700}]}
  with patch.object(s,'cached_json',return_value=d):
   values,status=s.load_scanx_caps({'TEST':'INE000000001','RIGHT':'INE000000002'})
  self.assertEqual(set(values),{'RIGHT'});self.assertEqual(status['fallback_symbols'],['TEST'])
 def test_all_provider_pages_required(self):
  d=self.mf();d.update(totalcount=2,totalpages=1)
  with patch.object(s,'cached_json',side_effect=lambda f,k,t,loader:loader()),patch.object(s,'read_bytes',return_value=json.dumps(d).encode()):
   with self.assertRaises(ValueError):s.mf_for_stock('INE000000001',s.day('2026-10-23'))

class ExchangeHistoryTests(unittest.TestCase):
 def test_official_close_replaces_entire_incomplete_daily_bar(self):
  import exchange_history as n
  now=datetime(2026,10,23,17,tzinfo=s.IST);chart=RuntimeTests().chart(now)
  chart['chart']['result'][0]['indicators']['quote'][0]['close'][-1]=None
  official={'INE000000001':[{'isin':'INE000000001','date':'2026-10-23','ohlc':[101,106,98,104],'source':'official-final'}]}
  merged=n.augment(chart,'INE000000001',official)
  payload,_,quality=s.price_payload(merged,'TEST',now)
  self.assertEqual(payload['rows'][-1][:5],['2026-10-23',101,106,98,104])
  self.assertTrue(quality['current_alerts_eligible']);self.assertEqual(quality['official_close_source'],'official-final')
  chart['chart']['result'][0]['meta']['symbol']='TEST.BO'
  with self.assertRaises(ValueError):n.augment(chart,'INE000000001',official)
 def test_archive_exact_date_isin_and_final_close_not_last_price(self):
  import exchange_history as n,io,zipfile
  date=s.day('2026-10-23')
  content='TradDt,Sgmt,Src,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,LastPric,SsnId\n2026-10-23,CM,NSE,INE000000001,TEST,RR,100,110,90,105,109,F1\n'
  def pack(text):
   b=io.BytesIO()
   with zipfile.ZipFile(b,'w') as z:z.writestr('BhavCopy_NSE_CM_0_0_0_20261023_F_0000.csv',text)
   return b.getvalue()
  result=n.parse_archive(pack(content),date,{'TEST':'INE000000001'})
  self.assertEqual(result['INE000000001']['ohlc'],[100,110,90,105])
  self.assertFalse(n.parse_archive(pack(content),date,{'OTHER':'INE000000002'}))
  with self.assertRaises(ValueError):n.parse_archive(pack(content.replace('2026-10-23','2026-10-22')),date,{'TEST':'INE000000001'})
 def test_compressed_state_migration_preserves_pending_and_history(self):
  import base64,gzip,lzma
  state={'version':1,'records':{'TEST':{'source':'dated proof '*50000}},'pending':[{'id':'a','text':'unsent'}],'delivered':['old-delivery']}
  store=object.__new__(s.CompactStore);store.state=copy.deepcopy(state)
  captured=[]
  with patch.object(s.Store,'save',lambda obj:captured.append(copy.deepcopy(obj.state))):store.save()
  self.assertEqual(captured[0]['encoding'],'xz-base64');self.assertEqual(json.loads(lzma.decompress(base64.b64decode(captured[0]['data']))),state)
  self.assertEqual(store.state,state)
  for encoding,compress in [('gzip-base64',gzip.compress),('xz-base64',lzma.compress)]:
   envelope={'version':1,'encoding':encoding,'data':base64.b64encode(compress(json.dumps(state).encode())).decode()}
   with patch.object(s.Store,'read',lambda obj:setattr(obj,'state',envelope)):store.read()
   self.assertEqual(store.state,state)

if __name__=='__main__':unittest.main()
