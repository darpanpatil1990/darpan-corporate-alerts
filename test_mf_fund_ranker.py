import gzip
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import mf_fund_ranker as m


def sample(name='Example Large Cap Fund', shares='120|120|100', weights='4.5|4.5|4.0'):
    return {'code': 0, 'headers': list(m.FIELDS), 'totalcount': 1, 'totalpages': 1,
            'data': [['12', name, '321', 'INF000000001', shares, weights,
                      0, 0, 0, '2026-10|2026-10|2026-09', 'example']]}


class MutualFundRankerTests(unittest.TestCase):
    def test_elss_arbitrage_and_defensive_types_excluded(self):
        for name in ['Nippon ELSS Tax Saver Fund', 'SBI Arbitrage Fund',
                     'XYZ Liquid Fund', 'XYZ Overnight Fund', 'XYZ Corporate Bond Fund',
                     'XYZ Equity Savings Fund', 'XYZ Balanced Advantage Fund',
                     'XYZ Multi Asset Allocation Fund', 'XYZ Gold ETF']:
            with self.subTest(name=name):
                self.assertIsNone(m.category(name)[0])
        for name in ['HDFC Mid Cap Fund', 'SBI Small Cap Fund', 'Axis Flexi Cap Fund',
                     'Nifty 50 Index Fund', 'Tech Thematic Fund', 'Value Fund']:
            with self.subTest(name=name):
                self.assertIsNotNone(m.category(name)[0])

    def test_uncertain_category_is_not_silently_included(self):
        self.assertEqual(m.category('Mystery Cashlike Strategy Fund'),
                         (None, 'Unclassified'))

    def test_no_monthly_quantity_change_still_counts(self):
        rows, identities = m.parse_page(sample(), 'INE000000001', date(2026, 11, 19))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['qty'], '120')
        self.assertEqual(rows[0]['weight_pct'], 4.5)
        self.assertEqual(len(identities), 1)

    def test_zero_current_quantity_does_not_count(self):
        rows, _ = m.parse_page(sample(shares='0|0|100'), 'INE000000001',
                               date(2026, 11, 19))
        self.assertEqual(rows, [])

    def test_stale_month_is_not_current_holding(self):
        rows, _ = m.parse_page(sample(), 'INE000000001', date(2027, 2, 19))
        self.assertEqual(rows, [])

    def test_conflicting_repeated_month_throws(self):
        with self.assertRaises(ValueError):
            m.parse_page(sample(shares='120|119|100'), 'INE000000001',
                         date(2026, 11, 19))

    def test_duplicate_stock_with_multiple_timeframes_counted_once(self):
        today = date(2026, 11, 19)
        def stock(*zones):
            return {'asof': '2026-11-18',
                    'quality': {'current_alerts_eligible': True},
                    'zones': list(zones)}
        z = lambda tf, achieve: {'tf': tf, 'date': '2025-03-01',
                                'd': 100, 'p': 110, 'achievement': achieve}
        record = {'amc': '1', 'scheme': 'Example Mid Cap Fund',
                  'scheme_id': '1', 'stock_isin': 'INE000000001',
                  'weight_pct': 3, 'period': '2026-10-31'}
        holdings = {'AAA': [record], 'BBB': [record]}
        stocks = {'AAA': stock(z('Monthly', 1), z('Quarterly', 2)),
                  'BBB': stock(z('Half Yearly', 0))}
        funds, excluded = m.ranking(holdings, stocks, today)
        self.assertEqual(len(funds), 1)
        self.assertEqual(funds[0]['zone_stocks'], 2)
        self.assertEqual(funds[0]['achievement_stocks'], 1)
        self.assertEqual((funds[0]['monthly'], funds[0]['quarterly'],
                          funds[0]['half_yearly']), (1, 1, 1))
        self.assertEqual(funds[0]['zone_pct_verified'], 100)
        self.assertEqual(excluded, {})

    def test_stale_quote_does_not_create_fake_zone_match(self):
        rec = {'asof': '2026-10-01',
               'zones': [{'tf': 'Monthly', 'date': '2025-01-01',
                          'd': 100, 'p': 110, 'achievement': 2}]}
        self.assertFalse(m.zone_info(rec, date(2026, 11, 19))['verified'])

    def test_state_decode(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'state.json'
            raw = {'records': {'ONE': {'zones': []}}}
            p.write_text(json.dumps({'encoding': 'gzip-base64',
                       'data': __import__('base64').b64encode(gzip.compress(
                           json.dumps(raw).encode())).decode()}))
            self.assertEqual(m.read_state(p), raw)


    def test_monthly_change_types_are_not_confused_with_returns(self):
        for prev,current,expected in [
            (100,120,'INCREASED'),(120,100,'REDUCED'),
            (100,100,'UNCHANGED'),(0,100,'NEW_POSITION'),(100,0,'EXITED')
        ]:
            with self.subTest(expected=expected):
                data=sample(shares=f'{current}|{current}|{prev}')
                rows,_=m.parse_page(data,'INE000000001',date(2026,11,19))
                self.assertEqual(rows[0]['change_type'],expected)
                self.assertEqual(float(rows[0]['change_qty']),current-prev)

    def test_exit_counted_as_activity_not_current_portfolio_stock(self):
        now=date(2026,11,19)
        rec={'asof':'2026-11-18','quality':{'current_alerts_eligible':True},
             'zones':[{'tf':'Monthly','date':'2025-01-01','d':100,'p':110,'achievement':1}]}
        row={'amc':'1','scheme':'Example Large Cap Fund','scheme_id':'1',
             'stock_isin':'INE000000001','weight_pct':0,
             'qty':'0','previous_qty':'100','change_qty':'-100',
             'change_type':'EXITED','period':'2026-10-31'}
        out,_=m.ranking({'AAA':[row]}, {'AAA':rec},now)
        self.assertEqual(out,[])  # no false zone stock when scheme sold entire stake

    def test_weight_tiebreak_favors_more_portfolio_exposure_not_past_nav(self):
        now=date(2026,11,19)
        zone={'tf':'Quarterly','date':'2025-01-01','d':100,'p':110,'achievement':1}
        rec={'asof':'2026-11-18','quality':{'current_alerts_eligible':True},'zones':[zone]}
        def row(name,weight):
            return {'amc':'1','scheme':name,'scheme_id':name,'qty':'100',
                    'period':'2026-10-31','weight_pct':weight}
        funds,_=m.ranking({'AAA':[row('Example Large Cap Fund',2.0),
                                       row('Example Small Cap Fund',6.0)]},
                          {'AAA':rec},now)
        self.assertEqual(funds[0]['fund'],'Example Small Cap Fund')
        self.assertEqual(funds[0]['zone_portfolio_weight_pct'],6.0)
        self.assertEqual(funds[0]['zone_stocks'],1)

    def test_html_escapes_embedded_source(self):
        source = {'funds': [{'fund': '</script><script>alert(1)</script>',
                            'amc_id': '1', 'category': 'Large Cap'}],
                  'coverage': {'fetched': 1, 'candidates': 1, 'partial': False},
                  'generated_at': '2026-11-19'}
        dashboard = m.html_dashboard(source)
        self.assertNotIn('</script><script>alert(1)', dashboard)
        self.assertIn('&#39;', dashboard)


if __name__ == '__main__':
    unittest.main()
