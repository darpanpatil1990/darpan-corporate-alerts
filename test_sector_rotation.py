import copy
import math
import unittest
import urllib.error
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock

import sector_rotation as s


class RotationTests(unittest.TestCase):
    def test_completed_week_cutoff(self):
        self.assertEqual(s.completed_friday(datetime(2026,10,23,17,59,tzinfo=s.IST)),date(2026,10,16))
        self.assertEqual(s.completed_friday(datetime(2026,10,23,18,tzinfo=s.IST)),date(2026,10,23))
        self.assertEqual(s.completed_friday(datetime(2026,10,26,12,tzinfo=s.IST)),date(2026,10,23))

    def test_population_zscore_and_no_lookahead(self):
        rs = [1+0.001*i+0.01*math.sin(i) for i in range(60)]
        self.assertAlmostEqual(s.zscore(list(range(14)))[-1],101.61245154965971)
        self.assertEqual(s.coordinates(rs[:40]),s.coordinates(rs)[:40])
        self.assertTrue(all(p==(None,None) for p in s.coordinates([1.0]*60)))
        rs[39]=None
        self.assertEqual(s.coordinates(rs)[40][1],None)

    def test_quadrants(self):
        self.assertEqual([s.quadrant(p) for p in [(101,101),(99,101),(101,99),(99,99),(100,99),(None,1)]],
                         ['Leading','Improving','Weakening','Lagging','Boundary','Unavailable'])

    def test_holiday_fallback_only_for_missing_archive(self):
        fetch = Mock(side_effect=[urllib.error.HTTPError('x',404,'missing',{},None),{'Nifty 500':10}])
        self.assertEqual(s.fetch_week(date(2026,10,23),Path('.'),fetch)['date'],'2026-10-22')
        fetch = Mock(side_effect=urllib.error.HTTPError('x',503,'outage',{},None))
        with self.assertRaises(urllib.error.HTTPError):
            s.fetch_week(date(2026,10,23),Path('.'),fetch)
        self.assertEqual(fetch.call_count,1)

    def test_source_validation(self):
        raw='Index Name,Index Date,Closing Index Value,Low Index Value,High Index Value\nNifty 500,23-10-2026,10,9,11\n'
        self.assertEqual(s.parse_csv(raw,date(2026,10,23)),{'Nifty 500':10})
        self.assertEqual(s.parse_csv(raw.replace(',10,9,11',',10,-,-'),date(2026,10,23)),{'Nifty 500':10})
        for bad in [raw.replace('23-10','22-10'),raw.replace(',10,9,11',',12,9,11'),raw.replace(',10,9,11',',nan,9,11'),raw.replace('Nifty 500','Nifty 50')]:
            with self.assertRaises(ValueError):
                s.parse_csv(bad,date(2026,10,23))

    def test_queue_survives_failure_and_deduplicates(self):
        store=Mock(state={'pending':[],'delivered':[]})
        saved=[]
        store.save.side_effect=lambda:saved.append(copy.deepcopy(store.state))
        failed=Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            s.deliver(store,'message','one','private-fingerprint',failed)
        self.assertEqual(saved[0]['pending'][0]['id'],'one')
        self.assertEqual(store.state['delivered'],[])
        send=Mock(return_value={'ok':True,'result':{'chat':{'type':'private'}}})
        self.assertEqual(s.deliver(store,'message','one','private-fingerprint',send),1)
        self.assertEqual(s.deliver(store,'message','one','private-fingerprint',send),0)
        self.assertEqual(send.call_count,1)
        with self.assertRaises(ValueError):
            s.deliver(store,'message','two','other',send)

    def test_non_private_response_not_checkpointed(self):
        store=Mock(state={'pending':[],'delivered':[]})
        with self.assertRaises(ValueError):
            s.deliver(store,'x','one','private',lambda _: {'ok':True,'result':{'chat':{'type':'group'}}})
        self.assertEqual(store.state['delivered'],[])
        self.assertEqual(len(store.state['pending']),1)

    def test_constituent_identity_and_equity_only(self):
        raw='Company Name,Symbol,Series,ISIN Code\nOne,ONE,EQ,INE000000001\nReit,REIT,RR,INE000000002\nPlaceholder,DUMMY,EQ,DUM000000001\n'
        self.assertEqual(s.constituent_members(raw),{'INE000000001':'ONE'})
        with self.assertRaises(ValueError):
            s.constituent_members(raw+'Duplicate,ONE,EQ,INE000000001\n')

    def test_exact_isin_join_strict_cap_and_missing(self):
        members={f'INE00000000{i}':'old'+str(i) for i in range(1,6)}
        universe={f'INE00000000{i}':'NEW'+str(i) for i in range(1,5)}
        caps={'NEW1':{'stock_isin':'INE000000001','cap':'500'},
              'NEW2':{'stock_isin':'INE000000002','cap':'500.01'},
              'NEW3':{'stock_isin':'INE000000009','cap':'10000'}}
        result=s.eligible_stocks(members,universe,caps)
        self.assertEqual(result,{'stocks':['NEW2'],'cap_unavailable':['NEW3','NEW4'],'cap_excluded':1})

    def test_related_message_groups_dedup_and_no_buy_inference(self):
        results={'Nifty Bank':{'group':'Leading'},'Nifty IT':{'group':'Lagging'}}
        data={'Nifty Bank':{'stocks':['ABC','XYZ'],'cap_unavailable':[],'cap_excluded':0},
              'Nifty IT':{'unavailable':'HTTPError'}}
        one=s.related_messages(results,data,'2026-10-23','2026-10-26')
        two=s.related_messages(results,data,'2026-10-23','2026-10-27')
        self.assertEqual(len(one),2)
        self.assertEqual([x[0] for x in one],[x[0] for x in two])
        self.assertIn('Bank (2): ABC, XYZ',one[0][1])
        self.assertIn('not stock RS ranking or a Buy signal',one[0][1])
        self.assertIn('source unavailable',one[1][1])
        data['Nifty Bank']['stocks'].append('NEW')
        self.assertNotEqual(one[0][0],s.related_messages(results,data,'2026-10-23','2026-10-27')[0][0])

    def test_related_message_pagination_no_silent_truncation(self):
        results={f'Nifty Test{i}':{'group':'Leading'} for i in range(20)}
        data={n:{'stocks':[f'STOCK{i:03d}' for i in range(30)],'cap_unavailable':[],'cap_excluded':0} for n in results}
        messages=s.related_messages(results,data,'2026-10-23','2026-10-26')
        self.assertGreater(len(messages),1)
        self.assertTrue(all(len(message)<4096 for _,message in messages))
        for name in results:
            self.assertEqual(sum(name.removeprefix('Nifty ')+ ' (30):' in message for _,message in messages),1)


if __name__=='__main__':
    unittest.main()
