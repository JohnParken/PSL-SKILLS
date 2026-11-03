"""Synthetic ISO date subset tests, not WPS producer evidence."""
from pathlib import Path
import sys
import unittest
from xml.parsers import expat
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_iso_date import parse_iso_date
from test_wps_ooxml_sheet import package, result

class IsoDateTests(unittest.TestCase):
    def test_exact_lexical_and_timezone(self):
        for raw, kind, offset in [('2024-02-29','date',None),('1976-11-22T08:30Z','datetime',0),('2024-02-29T23:59:59.123456789+14:00','datetime',840),('0001-01-01T00:00-00:30','datetime',-30)]:
            item=parse_iso_date(raw)
            self.assertEqual((item['lexical'],item['kind'],item['offset_minutes']),(raw,kind,offset))
            self.assertFalse(item['serial_conversion_performed'])
    def test_bad_calendar_clock_and_zone(self):
        for raw in ('1900-02-29','2023-02-29','0000-01-01','2024-13-01','2024-02-30','2024-01-01T24:00Z','2024-01-01T23:59:60Z','2024-01-01T12:60Z','2024-01-01T12:00+14:01','2024-01-01T12:00+00:60'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): parse_iso_date(raw)
    def test_bounded_lexical_subset(self):
        for raw in (' 2024-01-01','20240101','-0001-01-01','10000-01-01','2024-W01-1','2024-001','2024-01-01T01:01:01.'+'1'*100000,'2024-01-01T12:00+1400'):
            with self.assertRaisesRegex(ValueError,'Unsupported'): parse_iso_date(raw)

@unittest.skipUnless(expat.version_info >= (2,6,0),'requires Expat >=2.6')
class IsoDatePackageTests(unittest.TestCase):
    def test_strict_transitional_keep_date_as_typed_text(self):
        raw='1976-11-22T08:30Z'
        for strict in (False,True):
            value=result(package('<c r="A1" t="d"><v>'+raw+'</v></c>',strict))
            self.assertEqual(value['status'],'partial',value)  # fixture's shared rich text is intentionally partial
            cell=value['sheets'][0]['cells']['A1']
            self.assertEqual((cell['type'],cell['value'],cell['source_cell_type']),('text',raw,'d'))
            self.assertEqual(cell['iso_date']['offset_minutes'],0)
            self.assertNotIn('numeric_interpretation',cell)
    def test_formula_cache_and_missing_are_distinct(self):
        value=result(package('<c r="A1" t="d"><f>TODAY()</f><v>2024-02-29</v></c><c r="B1" t="d"><f>TODAY()</f></c>'))
        self.assertEqual(value['status'],'partial')
        cells=value['sheets'][0]['cells']
        self.assertEqual(cells['A1']['cache_status'],'unverified')
        self.assertEqual(cells['A1']['value'],'2024-02-29')
        self.assertEqual(cells['B1']['cache_status'],'missing')
        self.assertNotIn('iso_date',cells['B1'])
    def test_invalid_dates_never_become_empty_success(self):
        for raw in ('','1900-02-29','2024-01-01T24:00Z'):
            value=result(package('<c r="A1" t="d"><v>'+raw+'</v></c>'))
            self.assertEqual(value['status'],'corrupt',value)
        value=result(package('<c r="A1" t="d"><v>-0001-01-01</v></c>'))
        self.assertEqual(value['status'],'unsupported')

if __name__=='__main__':unittest.main()
