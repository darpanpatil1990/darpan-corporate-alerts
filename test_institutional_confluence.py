import unittest
from datetime import date,datetime
from institutional_confluence import compare_portfolios,overlaps,trade_rows,price_rows,nippon_links,IST

class EvidenceTests(unittest.TestCase):
    def test_quantity_not_weight(self):
        old={'S':{'name':'Fund','values':{'ISIN':100}}};new={'S':{'name':'Fund','values':{'ISIN':110}}}
        result=compare_portfolios(old,new,date(2026,8,31),date(2026,9,30),{'ISIN':'STOCK'},['old','new'])
        self.assertEqual(result['STOCK'][0]['change'],10)
        new['S']['values']['ISIN']=99
        self.assertEqual(compare_portfolios(old,new,date(2026,8,31),date(2026,9,30),{'ISIN':'STOCK'},['old','new']),{})
    def test_no_new_scheme_false_buy(self):
        self.assertEqual(compare_portfolios({}, {'NEW':{'name':'Fund','values':{'ISIN':5}}},date(2026,8,31),date(2026,9,30),{'ISIN':'S'},['a','b']),{})
    def test_temporal_overlap_and_split(self):
        zone={'latestTouch':'2026-10-01'};ev={'kind':'MF monthly quantity increase','start':'2026-08-31','end':'2026-09-30'}
        self.assertFalse(overlaps(zone,ev,'2026-10-23',[]))
        zone['latestTouch']='2026-09-15';self.assertTrue(overlaps(zone,ev,'2026-10-23',[]))
        self.assertFalse(overlaps(zone,ev,'2026-10-23',['2026-09-16']))
        self.assertFalse(overlaps(zone,ev,'2026-09-20',[]))
    def test_sell_never_buy(self):
        def table(action):return '<main><table id="result_list"><thead><tr>'+''.join('<th>'+h+'</th>' for h in ['Date','Client Name','Trade Type','Quantity','Company'])+'</tr></thead><tbody><tr><td>22 Oct 2026</td><td>Named Buyer</td><td>'+action+'</td><td>100</td><td><a href="/company/TCS/">TCS</a></td></tr></tbody></table></main>'
        self.assertEqual(trade_rows(table('Sell'),'Bulk',['TCS'],date(2026,10,23),'source'),[])
        self.assertEqual(len(trade_rows(table('Buy'),'Bulk',['TCS'],date(2026,10,23),'source')),1)
        self.assertEqual(trade_rows(table('Buy'),'Bulk',['OTHER'],date(2026,10,23),'source'),[])
    def test_nonconsecutive_months_rejected(self):
        html=''.join('<a href="/InvestorServices/FactsheetsDocuments/NIMF-MONTHLY-PORTFOLIO-'+d+'.xls">x</a>' for d in ['30-Sep-26','31-Jul-26'])
        with self.assertRaises(ValueError):nippon_links(html,date(2026,10,23))
    def test_monthly_price_rejected(self):
        obj={'chart':{'result':[{'meta':{'symbol':'TCS.NS','currency':'INR','dataGranularity':'1mo'}}]}}
        with self.assertRaises(ValueError):price_rows(obj,'TCS',datetime(2026,10,23,tzinfo=IST))

if __name__=='__main__':unittest.main()
