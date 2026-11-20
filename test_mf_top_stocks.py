"""Regression tests for unique-stock AMC buying confluence display."""
import unittest
import mf_top_stocks as m


def fund(name, amc, stock="AAA", change="INCREASED"):
    return {
        "fund": name, "amc_id": amc, "rank": 1,
        "stocks": [{"stock": stock, "verified": True, "period": "2026-10-31"}],
        "month_change_details": [{
            "stock": stock, "kind": change, "asof": "2026-10-31",
        }] if change else [],
    }


def stock(asof="2026-11-19"):
    return {
        "cmp": 105.0, "asof": asof,
        "quality": {"current_alerts_eligible": True, "quote": {
            "kind": "Last completed close", "asof": asof}},
        "zones": [
            {"tf": "Monthly", "d": 100, "p": 110, "date": "2025-01-31",
             "achievement": 2, "broken": False},
            {"tf": "Quarterly", "d": 90, "p": 108, "date": "2024-12-31",
             "achievement": 1, "broken": False},
        ],
        "evidence": [
            {"zone": {"tf": "Monthly"}, "evidence": [
                {"kind": "Bulk disclosed acquisition", "buyer": "Example Mutual Fund",
                 "end": "2026-11-18", "source": "https://www.screener.in/trades/bulk/"}]},
        ],
    }


class TopStocksTests(unittest.TestCase):
    def test_two_timeframes_one_stock_and_distinct_amcs(self):
        r = {"generated_at": "2026-11-20T12:00:00+05:30",
             "funds": [fund("One Large Cap Fund", "101"),
                       fund("Two Small Cap Fund", "102")]}
        out = m.build_top_stocks(r, {"records": {"AAA": stock()}})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["buying_funds"], 2)
        self.assertEqual(out[0]["buying_amcs"], 2)
        self.assertEqual(out[0]["holding_amcs"], 2)
        self.assertEqual(out[0]["status"], "IN_ZONE")
        self.assertEqual(out[0]["timeframes"], ["Monthly", "Quarterly"])
        self.assertEqual(len(out[0]["disclosed_bulk_block_mf_buys"]), 1)

    def test_no_monthly_increase_is_not_a_buy(self):
        r = {"generated_at": "2026-11-20T12:00:00+05:30",
             "funds": [fund("One Large Cap Fund", "101", change=None)]}
        out = m.build_top_stocks(r, {"records": {"AAA": stock()}})
        self.assertEqual(out[0]["buying_funds"], 0)
        self.assertEqual(out[0]["holding_funds"], 1)

    def test_stale_quote_filtered_and_unrelated_fund_not_a_buy(self):
        r = {"generated_at": "2026-11-20T12:00:00+05:30",
             "funds": [fund("One Large Cap Fund", "101")]}
        self.assertEqual(m.build_top_stocks(r, {"records": {"AAA": stock("2026-10-01")}}), [])
        s = stock()
        s["evidence"] = [{"zone": {}, "evidence": [
            {"kind": "Block disclosed acquisition", "buyer": "Some Private Limited",
             "end": "2026-11-18", "source": "https://www.screener.in/trades/block/"}]}]
        out = m.build_top_stocks(r, {"records": {"AAA": s}})
        self.assertEqual(out[0]["disclosed_bulk_block_mf_buys"], [])

    def test_no_unverified_post_touch_rally(self):
        z = {"tf": "Monthly", "d": 90, "p": 100, "latestTouch": "2026-09-03"}
        self.assertEqual(m.classify(110, [z], False), [])
        hit = m.classify(110, [z], True)
        self.assertEqual(hit[0]["status"], "POST_TOUCH_CLOSE_5_PLUS")

    def test_dashboard_escape_and_safety(self):
        r = {"generated_at": "2026-11-20", "top_stocks": [
            {"symbol": "</script><script>alert(1)</script>"}]}
        x = m.segment(r)
        self.assertNotIn("</script><script>alert(1)", x)
        self.assertIn("top-stock-segment", x)


if __name__ == "__main__":
    unittest.main()
