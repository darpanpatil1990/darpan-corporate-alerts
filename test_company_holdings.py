import unittest
import urllib.error
from unittest.mock import patch
from datetime import date
from company_holdings import parse_company, rising, signal_id, universe_from_csv
from company_holdings import read_company, request

HTML = '''<h1>Example company</h1><a href="https://www.nseindia.com/">NSE: TEST</a>
<ul id="top-ratios"><li>Market Cap ₹ 1,000 Cr.</li></ul>
<div id="quarterly-shp"><table><thead><tr><th></th><th>Jun 2026</th><th>Sep 2026</th></tr></thead>
<tbody><tr><td>FIIs +</td><td>9.07%</td><td>9.01%</td></tr>
<tr><td>DIIs +</td><td>13.41%</td><td>13.57%</td></tr></tbody></table></div>'''


class OwnershipTests(unittest.TestCase):
    def parse(self, html=HTML, symbol='TEST'):
        return parse_company(html, symbol, date(2026, 10, 23))

    def test_comparison_and_identity(self):
        record = self.parse()
        self.assertEqual(record['holdings']['FIIs']['change_pp'], '-0.06')
        self.assertEqual(record['holdings']['DIIs']['change_pp'], '0.16')
        self.assertTrue(rising(record))
        self.assertEqual(signal_id(record), signal_id(dict(record, checked_at='later', market_cap_cr='2000')))

    def test_unknown_or_invalid_data_rejected(self):
        for invalid in (HTML.replace('13.57%', ''), HTML.replace('Sep 2026', 'Sep 2027'),
                        HTML.replace('Jun 2026', 'Mar 2026'), HTML.replace('13.57%', '101.00%'),
                        HTML.replace('NSE: TEST', 'NSE: OTHER')):
            with self.subTest(html=invalid):
                with self.assertRaises(ValueError):
                    self.parse(invalid)

    def test_market_cap_boundary_excluded(self):
        self.assertIsNone(self.parse(HTML.replace('1,000', '500')))

    def test_interim_month_does_not_replace_quarter_or_break_comparison(self):
        html=HTML.replace('<th>Sep 2026</th>','<th>Sep 2026</th><th>Oct 2026</th>')
        html=html.replace('9.01%</td>','9.01%</td><td>99.00%</td>').replace('13.57%</td>','13.57%</td><td>0.00%</td>')
        result=self.parse(html)
        self.assertEqual(result['previous_period'],'Jun 2026')
        self.assertEqual(result['period'],'Sep 2026')
        self.assertEqual(result['holdings']['DIIs']['change_pp'],'0.16')

    def test_constituent_validation(self):
        csv = 'Symbol\n' + '\n'.join('STOCK' + str(i) for i in range(500))
        self.assertEqual(len(universe_from_csv(csv)), 500)
        with self.assertRaises(ValueError):
            universe_from_csv(csv + '\nSTOCK0')

    def test_consolidated_fallback_is_only_for_missing_page(self):
        error = urllib.error.HTTPError('https://www.screener.in/', 404, 'Missing', {}, None)
        with patch('company_holdings.request', side_effect=[error, HTML]) as mocked:
            record = read_company('TEST')
        self.assertIn('/consolidated/#shareholding', record['source'])
        self.assertEqual(mocked.call_count, 2)
        denied = urllib.error.HTTPError('https://www.screener.in/', 403, 'Denied', {}, None)
        with patch('company_holdings.request', side_effect=denied) as mocked:
            with self.assertRaises(urllib.error.HTTPError):
                read_company('TEST')
        self.assertEqual(mocked.call_count, 1)

    def test_delivery_write_is_never_automatically_retried(self):
        error = urllib.error.HTTPError('https://api.telegram.org/', 503, 'Unavailable', {}, None)
        with patch('company_holdings.urllib.request.urlopen', side_effect=error) as mocked:
            with self.assertRaises(urllib.error.HTTPError):
                request('https://api.telegram.org/', {'text': 'test'})
        self.assertEqual(mocked.call_count, 1)

    def test_transient_network_reads_recover_with_bounded_backoff(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        errors = [urllib.error.URLError('temporary outage'), TimeoutError(), response]
        with patch('company_holdings.urllib.request.urlopen', side_effect=errors) as mocked, patch('company_holdings.time.sleep') as sleep:
            self.assertEqual(request('https://example.test/data'), '{"ok":true}')
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    def test_persistent_network_read_stops_after_three_attempts(self):
        with patch('company_holdings.urllib.request.urlopen', side_effect=urllib.error.URLError('outage')) as mocked, patch('company_holdings.time.sleep'):
            with self.assertRaises(urllib.error.URLError):
                request('https://example.test/data')
        self.assertEqual(mocked.call_count, 3)

    def test_ambiguous_delivery_timeout_is_not_replayed(self):
        with patch('company_holdings.urllib.request.urlopen', side_effect=TimeoutError()) as mocked:
            with self.assertRaises(TimeoutError):
                request('https://api.telegram.org/', {'text': 'test'})
        self.assertEqual(mocked.call_count, 1)


if __name__ == '__main__':
    unittest.main()

