import copy,json,unittest
from datetime import datetime,timedelta
from unittest.mock import patch
import scanner_runtime as s

class RuntimeTests(unittest.TestCase):
 def mf(self):
  return {'code':0,'headers':['amcid','sch','planid','isin','shares','assetPerc','onemonthSharesCh','onemonthCh','threemonthCh','date','seo'],'data':[['1','Fund','22','INF000000001','120|120|100|100','2|2|5|5',20,-3,0,'2026-09|2026-09|2026-08|2026-08','fund']]}
 def test_quantity_not_weight_and_identical_month_duplicates(self):
  items,ids=s.mf_page_items(self.mf(),'INE000000001',s.day('2026-10-23'))
  self.assertEqual(items[0]['change'],20);self.assertEqual(items[0]['end'],'2026-09-30');self.assertEqual(ids,{'22:INF000000001'})
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
 def test_all_provider_pages_required(self):
  d=self.mf();d.update(totalcount=2,totalpages=1)
  with patch.object(s,'cached_json',side_effect=lambda f,k,t,loader:loader()),patch.object(s,'read_bytes',return_value=json.dumps(d).encode()):
   with self.assertRaises(ValueError):s.mf_for_stock('INE000000001',s.day('2026-10-23'))

if __name__=='__main__':unittest.main()
