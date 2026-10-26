import unittest
from datetime import datetime,timedelta

import personal_study as p
import test_scanner_runtime as fixtures


class PersonalStudyTests(unittest.TestCase):
    def test_private_state_encryption_and_integrity(self):
        from cryptography.exceptions import InvalidTag
        state={'pending':[{'text':'Private personal study'}],'delivered':['id']}
        one=p.seal(state,'test-only-secret','repository/state')
        two=p.seal(state,'test-only-secret','repository/state')
        self.assertNotEqual(one['data'],two['data'])
        self.assertNotIn('Private personal study',str(one))
        self.assertEqual(p.unseal(one,'test-only-secret','repository/state'),state)
        with self.assertRaises(InvalidTag):p.unseal(one,'wrong-key','repository/state')
        with self.assertRaises(InvalidTag):p.unseal(one,'test-only-secret','other-repository/state')
    def fixture(self):
        stock,matched,ref=fixtures.SeparateEntryAlertTests().fixture()
        stock['evidence']=[{'zone':z,'evidence':items} for z,items in matched]
        stock['market_cap_cr']='1000'
        return stock

    def test_completed_snapshot_freshness(self):
        now=datetime(2026,10,26,8,tzinfo=p.IST)
        state={'scan_progress':{'complete':True,'finished_at':(now-timedelta(minutes=30)).isoformat()}}
        self.assertEqual(p.valid_snapshot(state,now),state['scan_progress']['finished_at'])
        for progress in [{'complete':False},{'complete':True,'finished_at':(now-timedelta(hours=3)).isoformat()}]:
            with self.assertRaises(ValueError):p.valid_snapshot({'scan_progress':progress},now)

    def test_relative_return_math_and_missing_sessions(self):
        benchmark={f'{i:03d}':100+i for i in range(80)}
        stock={d:2*v for d,v in benchmark.items()}
        self.assertTrue(all(abs(v)<1e-10 for v in p.relative_returns(stock,benchmark,'079').values()))
        stock['079']*=1.1
        self.assertAlmostEqual(p.relative_returns(stock,benchmark,'079')['1W'],10)
        del stock['077']
        self.assertTrue(all(v is None for v in p.relative_returns(stock,benchmark,'079').values()))

    def test_sector_exact_identity_and_staleness(self):
        stock=self.fixture();now=datetime(2026,10,26,8,tzinfo=p.IST)
        snap={'generated_at':now.isoformat(),'week':'2026-10-23',
              'related':{'Nifty Bank':{'stock_isins':{stock['symbol']:stock['quality']['stock_isin']}}},
              'results':{'Nifty Bank':{'group':'Leading','previous':'Improving'}}}
        self.assertIn('Improving → Leading',p.sector_context(stock,snap,now)[0])
        snap['related']['Nifty Bank']['stock_isins'][stock['symbol']]='OTHER'
        self.assertIn('Unavailable',p.sector_context(stock,snap,now)[0])
        snap['week']='2026-10-16'
        self.assertIn('Unavailable',p.sector_context(stock,snap,now)[0])

    def test_full_disclaimer_and_original_gates(self):
        stock=self.fixture();now=datetime(2026,10,26,8,tzinfo=p.IST)
        item=p.study_alert(stock,None,now,'Stock strength: Unavailable')
        self.assertTrue(item['text'].startswith('Darpan Personal STUDY\n'))
        self.assertTrue(item['text'].endswith(p.DISCLAIMER))
        self.assertLessEqual(len(item['text']),4096)
        self.assertIn('Quarterly EPS: Unavailable',item['text'])
        stock['entry_references'][0]['achievement']=0
        self.assertIsNone(p.study_alert(stock,None,now,'RS positive'))

    def test_quote_strength_changes_do_not_resend(self):
        stock=self.fixture();now=datetime(2026,10,26,8,tzinfo=p.IST)
        one=p.study_alert(stock,None,now,'RS +1')
        stock['cmp']+=0.01
        two=p.study_alert(stock,None,now,'RS +2')
        self.assertEqual(one['id'],two['id'])
        stock['market_cap_cr']='500'
        self.assertIsNone(p.study_alert(stock,None,now,'RS +2'))

    def test_rs55_srs123_anchor_and_fresh_joint_cross(self):
        days=[f'2024-{i:03d}' for i in range(150)]
        benchmark={d:100+i for i,d in enumerate(days)}
        stock={d:2*v for d,v in benchmark.items()}
        result=p.rs_study_values(stock,benchmark,days[-1],anchor=days[0])
        self.assertAlmostEqual(result['srs123'],0)
        stock[days[-1]]*=1.1
        result=p.rs_study_values(stock,benchmark,days[-1],anchor=days[0])
        self.assertAlmostEqual(result['srs123'],10)
        self.assertAlmostEqual(result['ars'],10)
        self.assertTrue(result['fresh_joint_cross'])
        del stock[days[-3]]
        self.assertIsNone(p.rs_study_values(stock,benchmark,days[-1],anchor=days[0])['srs123'])

    def test_eps_exact_quarter_yoy_turnaround_and_identity(self):
        html='<a href="https://www.nseindia.com/">NSE: TEST</a><section id="quarters"><table><thead><tr><th></th><th>Sep 2025</th><th>Jun 2026</th><th>Sep 2026</th></tr></thead><tbody><tr><td>EPS in Rs</td><td>2.00</td><td>2.50</td><td>3.00</td></tr></tbody></table></section>'
        result=p.eps_from_html(html,'TEST',datetime(2026,10,26).date())
        self.assertEqual(result['quarter'],'2026-09-30')
        self.assertEqual(result['comparison'],'YoY +50.00%')
        result=p.eps_from_html(html.replace('>2.00<','>-2.00<'),'TEST',datetime(2026,10,26).date())
        self.assertEqual(result['comparison'],'Loss → profit turnaround')
        with self.assertRaises(ValueError):p.eps_from_html(html,'OTHER',datetime(2026,10,26).date())


if __name__=='__main__':unittest.main()
