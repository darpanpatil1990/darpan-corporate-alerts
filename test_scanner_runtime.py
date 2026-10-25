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

class NseAcquisitionTests(unittest.TestCase):
 def fixture(self,**changes):
  import exchange_disclosures as n
  values={'buyer':'Disclosed Buyer','instrument':'Equity','category':'Promoter','action':'Buy','qty':'1000','start':'2026-10-20','end':'2026-10-21','mode':'Market Purchase','venue':'NSE'}
  values.update(changes)
  facts=''.join(f'<{n.FIELDS[k]} contextRef="Disclosure1">{v}</{n.FIELDS[k]}>' for k,v in values.items())
  blob=('<xbrl><Symbol contextRef="MainI">TEST</Symbol><ISINCode contextRef="MainI">INE000000001</ISINCode>'+facts+'</xbrl>').encode()
  filing={'symbol':'TEST','appId':'1','broadcastDateTime':'23-Oct-2026 21:18:34','xmlFileName':'https://nsearchives.nseindia.com/corporate/xbrl/test.xml'}
  return n,blob,filing
 def test_xbrl_transaction_dates_are_separate_from_later_publication(self):
  n,blob,filing=self.fixture();result=n.parse_pit(blob,filing,'INE000000001')
  event=result['events'][0]
  self.assertEqual(event['start'],'2026-10-20');self.assertEqual(event['end'],'2026-10-21')
  self.assertEqual(event['reported_at'],'23-Oct-2026 21:18:34');self.assertEqual(event['buyer_category'],'Promoter')
 def test_gift_allotment_warrants_and_sales_are_excluded(self):
  for changed in ({'mode':'Gift'},{'mode':'Preferential Offer'},{'instrument':'Warrant'},{'action':'Sell'}):
   n,blob,filing=self.fixture(**changed)
   self.assertFalse(n.parse_pit(blob,filing,'INE000000001')['events'])
 def test_derivative_only_disclosure_is_verified_without_inventing_equity_buy(self):
  n,blob,filing=self.fixture(instrument='Derivative')
  blob=blob.replace(b'<SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure1">Buy</SecuritiesAcquiredOrDisposedTransactionType>',b'')
  parsed=n.parse_pit(blob,filing,'INE000000001')
  self.assertEqual(parsed['contexts'],1);self.assertFalse(parsed['events']);self.assertEqual(parsed['excluded']['Not equity/REIT/InvIT units'],1)
 def test_wrong_isin_future_dates_conflicting_facts_and_invalid_quantity_fail(self):
  n,blob,filing=self.fixture()
  with self.assertRaises(ValueError):n.parse_pit(blob,filing,'INE000000002')
  for changed in ({'end':'2026-10-24'},{'start':'2026-10-22'},{'qty':'1.5'}):
   n,blob,filing=self.fixture(**changed)
   with self.assertRaises(ValueError):n.parse_pit(blob,filing,'INE000000001')
  n,blob,filing=self.fixture();blob=blob.replace(b'</xbrl>',b'<ISINCode contextRef="MainI">INE000000002</ISINCode></xbrl>')
  with self.assertRaises(ValueError):n.parse_pit(blob,filing,'INE000000001')
 def test_whole_transaction_range_must_fall_inside_zone_visit(self):
  n,blob,filing=self.fixture();event=n.parse_pit(blob,filing,'INE000000001')['events'][0]
  self.assertFalse(s.acquisition_overlaps({'latestTouch':'2026-10-21'},event,'2026-10-23',[]))
  self.assertTrue(s.acquisition_overlaps({'latestTouch':'2026-10-20'},event,'2026-10-23',[]))
 def test_second_publication_of_same_purchase_is_not_another_alert(self):
  n,blob,filing=self.fixture();event=n.parse_pit(blob,filing,'INE000000001')['events'][0]
  duplicate=dict(event,source='https://nsearchives.nseindia.com/corporate/xbrl/revision.xml',reported_at='24-Oct-2026 12:00:00',filing_id='2:Disclosure1')
  self.assertEqual(s.trade_identity('TEST',event),s.trade_identity('TEST',duplicate))
  self.assertNotEqual(s.trade_identity('TEST',event),s.trade_identity('TEST',dict(event,qty='1001')))
 def test_withdrawn_evidence_does_not_replay_a_delivered_subset(self):
  n,blob,filing=self.fixture();event=n.parse_pit(blob,filing,'INE000000001')['events'][0]
  other=dict(event,buyer='Another buyer');zone={'tf':'Monthly','date':'2026-09-01','latestTouch':'2026-10-19'}
  old=[(zone,[event,other])];previous={'evidence':[{'zone':zone,'evidence':[event,other]}]}
  delivered={s.match_identity('TEST',old)}
  self.assertTrue(s.delivered_superset('TEST',previous,[(zone,[event])],delivered))
  self.assertFalse(s.delivered_superset('TEST',previous,[(zone,[dict(event,qty='1001')])],delivered))
 def test_latest_revision_supersedes_original_filing(self):
  n,blob,old=self.fixture();new=dict(old,appId='2',prevAppId='1',xmlFileName='https://nsearchives.nseindia.com/corporate/xbrl/revision.xml');old['prevAppId']=None
  parsed=n.parse_pit(blob,new,'INE000000001');calls=[]
  def fetch(target):return json.dumps({'data':[] if 'invitsreits' in target else [old,new]}).encode()
  def cached(folder,key,ttl,loader):calls.append(key);return parsed
  events,status=n.load('Insider',{'TEST':'INE000000001'},s.day('2026-10-24'),fetch,cached)
  self.assertEqual(status['selected_filings'],1);self.assertEqual(status['superseded_filings'],1);self.assertEqual(len(calls),1);self.assertEqual(len(events),1)
 def test_sast_actual_date_and_mode_are_validated(self):
  import exchange_disclosures as n
  row={'symbol':'TEST','acqSaleType':'Acquisition','acquisitionMode':'Open Market','acqType':'Equity shares','acquirerDate':'20-OCT-2026 to 21-OCT-2026','sysTime':'23-Oct-2026 17:05','noOfShareAcq':'1,000','attachement':'https://nsearchives.nseindia.com/corporate/test.zip','acquirerName':'Disclosed Buyer','application_no':'1','promoterType':'Y'}
  events,status=n.parse_sast([row,dict(row,acquisitionMode='Inter-se transfer')],{'TEST':'INE000000001'},s.day('2026-10-24'))
  self.assertFalse(status['errors']);self.assertEqual(len(events),1);self.assertEqual(events[0][1]['qty'],'1000');self.assertEqual(events[0][1]['end'],'2026-10-21')

class UniverseRecoveryTests(unittest.TestCase):
 def fixture(self):
  now=datetime(2026,10,24,17,tzinfo=s.IST)
  members={f'TEST{i}':f'INE{i:09d}' for i in range(500)}
  state={'last_batch':{'date':(now-timedelta(days=1)).isoformat(),'checked':list(members)+['DUMMYHEG'],'failed':{},'excluded':{'DUMMYHEG':'Official index placeholder without a tradable stock ISIN/price history'},'full_universe_attempted':True},'records':{k:{'quality':{'stock_isin':v}} for k,v in members.items()}}
  return now,members,state
 def test_official_source_success_records_exact_membership_proof(self):
  now,members,state=self.fixture();raw='Symbol,ISIN Code\n'+''.join(k+','+v+'\n' for k,v in members.items())
  with patch.object(s,'read_bytes',return_value=raw.encode()):symbols,isin,status=s.load_universe(now,state)
  self.assertEqual(isin,members);self.assertEqual(len(symbols),500);self.assertTrue(status['current_source_verified'])
  self.assertEqual(state['universe_snapshot']['verified_at'],now.isoformat())
 def test_redirect_outage_uses_dated_full_scan_without_renewing_its_age(self):
  now,members,state=self.fixture();verified=state['last_batch']['date']
  with patch.object(s,'read_bytes',side_effect=OSError('redirect loop')):
   symbols,isin,status=s.load_universe(now,state)
   _,_,later=s.load_universe(now+timedelta(days=1),state)
  self.assertEqual(isin,members);self.assertFalse(status['current_source_verified']);self.assertEqual(status['original_member_count'],501)
  self.assertEqual(status['omitted_placeholders'],['DUMMYHEG']);self.assertEqual(later['verified_at'],verified)
  self.assertEqual(later['age_hours'],48)
 def test_stale_or_incomplete_membership_never_silently_becomes_universe(self):
  now,_,state=self.fixture();state['records'].pop('TEST3')
  with patch.object(s,'read_bytes',side_effect=OSError('source unavailable')):
   with self.assertRaises(ValueError):s.load_universe(now,state)
  _,_,state=self.fixture();state['last_batch']['date']=(now-timedelta(days=8)).isoformat()
  with patch.object(s,'read_bytes',side_effect=OSError('source unavailable')):
   with self.assertRaises(ValueError):s.load_universe(now,state)
 def test_unverified_or_duplicate_stock_identity_is_rejected(self):
  _,members,_=self.fixture();members['TEST1']=members['TEST0']
  with self.assertRaises(ValueError):s.validate_members(members)

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

class AlertUpgradeTests(unittest.TestCase):
 def stock(self):
  return {'symbol':'TEST','asof':'2026-09-15','cmp':100,'price_source':'https://official.test/close','quality':{'stock_isin':'INE000000001','exchange':'NSE','quote':{'kind':'Close','asof':'2026-09-15'}},'zones':[]}
 def zone(self):return {'tf':'Monthly','date':'2026-01-01','d':90,'p':110,'latestTouch':'2026-09-10','firstReturn':True,'testEpisodes':1}
 def evidence(self):return {'kind':'MF monthly quantity increase','buyer':'Fund','amc_id':'1','start':'2026-08-31','end':'2026-09-30','qty':100,'change':20,'source':'https://scanx.trade/'}
 def test_exchange_proof_not_hidden_by_many_fund_schemes(self):
  items=[dict(self.evidence(),buyer='Fund '+str(i)) for i in range(20)]
  items.append({'kind':'Insider acquisition','buyer':'Explicit buyer','buyer_category':'Promoter group','start':'2026-09-12','end':'2026-09-12','qty':500,'mode':'Market Purchase','date_basis':'NSE transaction period','reported_at':'2026-09-14','source':'https://nsearchives.nseindia.com/corporate/proof.xml'})
  text=s.alert_text(self.stock(),[(self.zone(),items)])
  self.assertIn('Explicit buyer',text);self.assertIn('Promoter group',text);self.assertIn('Published 2026-09-14',text);self.assertIn('proof.xml',text);self.assertIn('not confirmed same-day buys',text);self.assertLessEqual(len(text),3950)
 def test_source_types_are_represented_before_second_same_type(self):
  items=[dict(self.evidence(),buyer=str(i)) for i in range(6)]+[dict(self.evidence(),kind='Bulk acquisition',buyer='Bulk',amc_id=None)]
  self.assertEqual(len({e['kind'] for e in s.display_evidence(items)[:2]}),2)
 def test_only_delivered_match_is_migrated_and_watch_survives_lost_match(self):
  stock=self.stock();matched=[(self.zone(),[self.evidence()])];stock['evidence']=[{'zone':z,'evidence':e} for z,e in matched]
  state={'records':{'TEST':stock},'delivered':[]};s.migrate_delivered_watches(state);self.assertFalse(state.get('zone_watches'))
  state['delivered']=[s.match_identity('TEST',matched)];s.migrate_delivered_watches(state);self.assertEqual(len(state['zone_watches']),1)
  stock['evidence']=[];s.migrate_delivered_watches(state);self.assertEqual(len(state['zone_watches']),1)
 def test_break_once_same_identity_only_and_not_on_failed_quote(self):
  stock=self.stock();watch=s.alert_watches(stock,[(self.zone(),[])])[0];state={'delivered':[],'pending':[]};s.remember_watches(state,[watch])
  event=dict(watch,close=89,breach_date='2026-09-30');stock['zone_breaks']=[event]
  stock['quality']['current_alerts_eligible']=False;s.queue_zone_breaks(state,stock);self.assertEqual(state['pending'],[])
  stock['quality']['current_alerts_eligible']=True;stock['quality']['stock_isin']='INE999999999';s.queue_zone_breaks(state,stock);self.assertEqual(state['pending'],[])
  stock['quality']['stock_isin']=watch['stock_isin'];s.queue_zone_breaks(state,stock);s.queue_zone_breaks(state,stock);self.assertEqual(len(state['pending']),1)
  self.assertIn('Completed Monthly candle',state['pending'][0]['text'])
  state['delivered']=[state['pending'].pop()['id']];s.queue_zone_breaks(state,stock);self.assertEqual(state['pending'],[])
 def test_watch_registered_only_after_successful_private_delivery(self):
  stock=self.stock();watch=s.alert_watches(stock,[(self.zone(),[])])[0];store=Mock();store.state={'pending':[{'id':'match','text':'Research','watches':[watch]}],'delivered':[]}
  with patch.object(s,'verify_private_destination',return_value='private'),patch.object(s,'request',return_value='{"ok":false}'),patch.dict(s.os.environ,{'BOT_TOKEN':'test','CHAT_ID':'test'}):
   with self.assertRaises(ValueError):s.deliver_pending(store,'private')
  self.assertFalse(store.state.get('zone_watches'))
  with patch.object(s,'verify_private_destination',return_value='private'),patch.object(s,'request',return_value='{"ok":true}'),patch.object(s.time,'sleep'),patch.dict(s.os.environ,{'BOT_TOKEN':'test','CHAT_ID':'test'}):s.deliver_pending(store,'private')
  self.assertIn(watch['id'],store.state['zone_watches'])
 def test_timeframe_candle_close_required_not_wick_or_open_month(self):
  import subprocess
  script="const {monitorZones}=require('./zone_runner.cjs');process.stdout.write(JSON.stringify(monitorZones(JSON.parse(process.argv[1]))));"
  watch=dict(self.zone(),id='watch',registered_asof='2026-09-15')
  rows=[['2026-08-31',100,105,95,100],['2026-09-16',100,105,80,95],['2026-09-30',95,98,85,89],['2026-10-01',89,95,80,85]]
  def check(asof,values=rows,missing=None):
   payload={'rows':values,'asof':asof,'monitoredZones':[watch],'dropFirstAggregate':True,'missingDates':missing or []}
   return json.loads(subprocess.check_output(['node','-e',script,json.dumps(payload)],text=True,cwd=s.ROOT))
  self.assertEqual(check('2026-09-30'),[])
  event=check('2026-10-01');self.assertEqual(event[0]['breach_date'],'2026-09-30');self.assertEqual(event[0]['close'],89)
  self.assertEqual(check('2026-10-01',missing=['2026-09-20']),[])
  rows[2][4]=91;self.assertEqual(check('2026-10-01'),[])

class TimeframeAlertTests(unittest.TestCase):
 stock=AlertUpgradeTests.stock
 zone=AlertUpgradeTests.zone
 evidence=AlertUpgradeTests.evidence
 def matches(self):
  return [(dict(self.zone(),tf=tf),[dict(self.evidence(),buyer=tf+' buyer')]) for tf in s.TIMEFRAME_CODES]
 def test_four_isolated_messages_and_price_tick_dedup(self):
  state={'pending':[],'delivered':[]};matched=self.matches();queued=set();delivered=set()
  s.queue_timeframe_alerts(state,self.stock(),matched,{},queued,delivered)
  self.assertEqual(len(state['pending']),4)
  for item,(tf,code) in zip(state['pending'],s.TIMEFRAME_CODES.items()):
   self.assertTrue(item['text'].startswith(code+' | '+tf+' Demand'))
   self.assertIn(tf+' buyer',item['text']);self.assertEqual({w['tf'] for w in item['watches']},{tf})
   for other in s.TIMEFRAME_CODES:
    if other!=tf:self.assertNotIn('\n'+other+' buyer\n',item['text'])
   self.assertIn('Also matching:',item['text']);self.assertLessEqual(len(item['text']),4096)
  s.queue_timeframe_alerts(state,dict(self.stock(),cmp=101),matched,{},queued,delivered)
  self.assertEqual(len(state['pending']),4)
 def test_new_proof_only_alerts_changed_timeframe(self):
  matched=self.matches();old={'evidence':[{'zone':z,'evidence':items} for z,items in matched]}
  ids=[s.match_identity('TEST',group) for group in s.timeframe_groups(matched).values()]
  state={'pending':[],'delivered':ids[:]};changed=copy.deepcopy(matched);changed[1][1].append(dict(self.evidence(),buyer='New quarterly buyer'))
  s.queue_timeframe_alerts(state,self.stock(),changed,old,set(),set(ids))
  self.assertEqual(len(state['pending']),1);self.assertEqual(state['pending'][0]['timeframe'],'Quarterly')
 def test_legacy_delivered_preserved_unsent_split_idempotently(self):
  matched=self.matches();stock=self.stock();stock['evidence']=[{'zone':z,'evidence':items} for z,items in matched]
  legacy=s.match_identity('TEST',matched)
  state={'records':{'TEST':stock},'pending':[],'delivered':[legacy]}
  s.migrate_timeframe_alerts(state);s.migrate_timeframe_alerts(state)
  self.assertEqual(len(state['delivered']),5)
  s.queue_timeframe_alerts(state,stock,matched,stock,set(),set(state['delivered']));self.assertEqual(state['pending'],[])
  state={'records':{'TEST':stock},'pending':[{'id':legacy,'text':'Old combined'}],'delivered':[]}
  s.migrate_timeframe_alerts(state);s.migrate_timeframe_alerts(state)
  self.assertEqual(len(state['pending']),4);self.assertEqual(state['delivered'],[])
 def test_removed_proof_does_not_resend(self):
  matched=self.matches();matched[0][1].append(dict(self.evidence(),buyer='Expired proof'))
  previous={'evidence':[{'zone':z,'evidence':items} for z,items in matched]}
  identity=s.match_identity('TEST',[matched[0]]);current=copy.deepcopy(matched);current[0][1].pop()
  state={'pending':[],'delivered':[identity]}
  s.queue_timeframe_alerts(state,self.stock(),current,previous,set(),{identity})
  self.assertEqual([item['timeframe'] for item in state['pending']],['Quarterly','Half Yearly','Yearly'])

class EntryReferenceTests(unittest.TestCase):
 def test_reference_mapping_closed_candles_and_never_ready(self):
  import subprocess
  script=r"""
const assert=require('assert'),engine=require('./zone_engine.cjs'),{entryReferences}=require('./zone_runner.cjs');
const parent={tf:'Monthly',date:'2026-01-01',d:90,p:120,currentInside:true,firstTouch:'2026-09-10',latestTouch:'2026-09-10'};
const child={tf:'Daily',date:'2026-09-01',departureEnd:'2026-09-02',d:98,p:102,currentInside:true,fresh:true,matches:[],achievement:1};
const rows=[['2026-09-10',100,104,99,100],['2026-09-11',100,114,99,112],['2026-09-12',101,103,99,100]];
const stock={symbol:'TEST',cmp:100,asof:'2026-09-12',zones:[parent,child],curves:{},trends:{Weekly:{state:'Up'}},htfContexts:[{...parent,id:'parent'}]};
const payload={rows,asof:stock.asof};const original=engine.supplies;
engine.supplies=()=>[];
assert.equal(entryReferences(stock,payload)[0].status,'WAIT_SUPPLY_CLOSE');
engine.supplies=()=>[{date:'2026-09-01',p:103,d:105,bodyIn:.8,bodyOut:.8}];
let r=entryReferences(stock,payload)[0];assert.equal(r.status,'REVIEW_REQUIRED');assert.equal(r.ready,false);assert.equal(r.entryReference,102);assert.equal(r.stopReference,98);assert.deepEqual(r.targets.map(t=>t.price),[110,114,118]);
rows[1][4]=104;r=entryReferences(stock,payload)[0];assert.equal(r.status,'WAIT_SUPPLY_CLOSE');
rows[1][4]=112;rows[1][5]=false;r=entryReferences(stock,payload)[0];assert.equal(r.status,'WAIT_SUPPLY_CLOSE');
rows[1][5]=true;stock.cmp=103;child.currentInside=false;r=entryReferences(stock,payload)[0];assert.equal(r.status,'WAIT_RETEST');
engine.supplies=()=>[];
for(const [tf,trend,entry] of [['Monthly','Weekly','Daily'],['Quarterly','Monthly','Weekly'],['Half Yearly','Quarterly','Monthly'],['Yearly','Half Yearly','Quarterly']]){
 stock.zones=[{...parent,tf}];r=entryReferences(stock,payload)[0];assert.equal(r.trendTF,trend);assert.equal(r.entryTF,entry);assert.equal(r.status,'WAIT_ENTRY_ZONE');assert.equal(r.ready,false);
}
engine.supplies=original;console.log('Entry reference assertions passed');
"""
  output=subprocess.check_output(['node','-e',script],text=True,cwd=s.ROOT)
  self.assertIn('assertions passed',output)
 def test_message_labels_levels_as_reference_and_fits_telegram(self):
  fixture=AlertUpgradeTests();stock=fixture.stock();z=fixture.zone()
  ref={'parentTF':'Monthly','parentDate':z['date'],'parentD':z['d'],'parentP':z['p'],'trendTF':'Weekly','trend':'Up','entryTF':'Daily','status':'WAIT_RETEST','zoneDate':'2026-09-01','distal':95,'proximal':105,'entryReference':105,'stopReference':95,'targets':[{'rr':r,'price':105+r*10} for r in (2,3,4)]}
  stock['entry_references']=[ref];items=[dict(fixture.evidence(),buyer='Fund '+str(i)+'x'*170) for i in range(30)]
  msg=s.timeframe_alert(stock,'Monthly',[(z,items)],[(z,items)])
  self.assertIn('NOT Entry Ready',msg['text']);self.assertIn('SL boundary INR 95.00',msg['text']);self.assertIn('1:2 125.00',msg['text']);self.assertIn('Buffers, risk sizing',msg['text']);self.assertLessEqual(len(msg['text']),4096)
  other=s.entry_reference_text(stock,'Quarterly',[(dict(z,tf='Quarterly'),items)])
  self.assertNotIn('105.00',other)

if __name__=='__main__':unittest.main()
