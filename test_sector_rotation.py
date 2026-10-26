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


if __name__=='__main__':
    unittest.main()
