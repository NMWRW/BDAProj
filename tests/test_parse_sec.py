"""Offline checks for section boundaries and narrative/table separation."""
import contextlib
import csv
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("parse_sec", ROOT / "scripts/parse_sections.py")
sec = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sec
spec.loader.exec_module(sec)


class ParserTests(unittest.TestCase):
    def test_numeric_table_is_separate_and_inline_facts_remain(self):
        html = '''<html><body><ix:hidden>SECRET FACT</ix:hidden>
        <div style="display: none">HIDDEN TEXT</div><p>PART I</p>
        <p>Item 1. Financial Statements</p><p>Revenue grew by
        <ix:nonFraction>12%</ix:nonFraction> this quarter.</p>
        <table><tr><td>Year</td><td>Revenue</td></tr>
        <tr><td>2025</td><td>123,456</td></tr>
        <tr><td>2024</td><td>100,000</td></tr></table>
        <p>Explanation following the table.</p>
        <p>Item 2. Management's Discussion</p><p>Economic outlook.</p>
        </body></html>'''
        sections = sec.extract_sections(html)
        self.assertEqual(len(sections), 2)
        self.assertIn("12% this quarter", sections[0].text)
        self.assertNotIn("123,456", sections[0].text)
        self.assertNotIn("SECRET", sections[0].text)
        self.assertNotIn("HIDDEN", sections[0].text)
        self.assertIn("Explanation following", sections[0].text)
        self.assertIn("123,456", sections[0].tables[0])
        included = sec.extract_sections(html, include_tables=True)
        self.assertIn("123,456", included[0].text)

    def test_layout_table_preserves_prose_and_nested_financial_table(self):
        paragraph = "The company faces significant operational risks. " * 10
        html = f'''<p>PART II</p><table><tr><td>Item 1A.</td><td>Risk Factors</td></tr></table>
        <table><tr><td><p>{paragraph}</p>
        <table><tr><td>2025</td><td>55,555</td></tr>
        <tr><td>2024</td><td>44,444</td></tr></table></td></tr></table>
        <p>Item 2. Unregistered Sales</p><p>Repurchase narrative.</p>'''
        section = sec.extract_sections(html)[0]
        self.assertEqual((section.part, section.item), ("II", "1A"))
        self.assertIn(paragraph.strip(), section.text)
        self.assertNotIn("55,555", section.text)
        self.assertEqual(len(section.tables), 1)

    def test_title_variation_and_duplicate_item_numbers(self):
        html = '''<p>PART I</p><p>Item 2. Management’s Discussion</p><p>MD&amp;A body.</p>
        <p>PART II</p><p>Item 1A. Risk Factors</p><p>Risk body.</p>
        <p>Item 2. Unregistered Sales of Equity Securities, Use of Proceeds,
        and Issuer Purchases of Equity Securities</p><p>Repurchase body.</p>
        <p>SIGNATURES</p><p>Chief executive signature.</p>'''
        sections = sec.extract_sections(html)
        self.assertEqual([(s.part, s.item) for s in sections], [("I", "2"), ("II", "1A"), ("II", "2")])
        self.assertNotIn("Repurchase", sections[1].text)
        self.assertIn("Repurchase body", sections[2].text)
        self.assertNotIn("signature", sections[2].text)

    def test_contents_and_inline_formatting(self):
        html = '''<table><tr><td><a href="#one">Item 1.</a> Statements</td></tr>
        <tr><td><a href="#two">Item 2.</a> Discussion</td></tr></table>
        <p>PART I</p><p id="one"><b>Item 1.</b> Financial Statements</p>
        <p>Actual financial narrative.</p><p id="two">Item 2. Discussion</p>
        <p>Actual discussion.</p>'''
        sections = sec.extract_sections(html)
        self.assertEqual(len(sections), 2)
        self.assertIn("Actual financial narrative", sections[0].text)
        self.assertFalse(sections[0].tables)

    def test_annual_and_current_report_item_numbers(self):
        annual = sec.extract_sections('<p>PART I</p><p>ITEM 1. BUSINESS</p><p>Business.</p>'
                                      '<p>ITEM 1A. RISK FACTORS</p><p>Risk.</p>', '10-K')
        self.assertEqual([s.item for s in annual], ['1', '1A'])
        current = sec.extract_sections('<p>Item 2.02 Results of Operations</p><p>Results.</p>'
                                       '<p>Item 9.01 Exhibits</p><p>Exhibit narrative.</p>', '8-K')
        self.assertEqual([s.item for s in current], ['2.02', '9.01'])

    def test_linked_body_headings_and_html_comments(self):
        html = '''<div id="toc">Contents</div><p>PART I</p>
        <p><a href="#toc">Item 1. Financial Statements</a></p>
        <!-- Item 9. Bogus heading -->
        <p>Visible body.</p><p>Item 2. Discussion</p><p>Discussion body.</p>'''
        sections = sec.extract_sections(html)
        self.assertEqual([s.item for s in sections], ['1', '2'])
        self.assertNotIn('Bogus', sections[0].text)

    def test_unlinked_contents_choose_body_and_separate_parts(self):
        html = '''<p>PART I</p><p>Item 1. Financial Statements</p><p>Item 2. Discussion</p>
        <p>PART II</p><p>Item 1. Legal Proceedings</p><p>Item 2. Unregistered Sales</p>
        <p>PART I</p><p>Item 1. Financial Statements</p><p>Actual financial narrative.</p>
        <p>Item 2. Discussion</p><p>Actual discussion narrative.</p>
        <p>PART II</p><p>Item 1. Legal Proceedings</p><p>Actual legal narrative.</p>
        <p>Item 2. Unregistered Sales</p><p>Actual repurchase narrative.</p>'''
        sections = sec.extract_sections(html)
        self.assertEqual(len(sections), 4)
        self.assertTrue(all('Actual' in section.text for section in sections))

    def test_all_downloaded_filings_when_available(self):
        manifests = list((ROOT / 'data/sec').glob('**/manifest.json'))
        if not manifests:
            self.skipTest('Local SEC downloads are optional')
        seen = set()
        for manifest in manifests:
            if json.loads(manifest.read_text()).get('status') != 'complete':
                continue
            for path, form in sec.filing_inputs(manifest):
                if path.name in seen:
                    continue
                seen.add(path.name)
                with self.subTest(file=path.name):
                    sections = sec.extract_sections(path.read_bytes(), form)
                    keys = [(s.part, s.item) for s in sections]
                    self.assertEqual(len(keys), len(set(keys)))
                    self.assertTrue(all('[[SEC_TABLE_' not in s.text for s in sections))
                    if form == '10-Q':
                        # Issuers may omit inapplicable items (Walmart omits
                        # Part II Items 3/4); an Apple-only count is not universal.
                        required = {('I', '1'), ('I', '2'), ('I', '3'), ('I', '4'),
                                    ('II', '1'), ('II', '1A'), ('II', '2'), ('II', '5'), ('II', '6')}
                        self.assertTrue(required.issubset(keys))
                    risk = next(s for s in sections if s.item == '1A')
                    # Some quarterlies only say that risks have not changed.
                    self.assertGreater(len(risk.text), len(risk.title) + 50)
                    if form == '10-Q':
                        self.assertNotIn('Purchases of Equity Securities by the Issuer', risk.text)

    def test_repeated_part_page_header_does_not_truncate_risk(self):
        html = '''<p>Part I</p><p>Item 1A. Risk Factors.</p>
        <p>The company faces risks to its customers,</p><p>Part I</p>
        <p>shareholders and employees. This continues on the next page.</p>
        <p>Item 1B. Unresolved Staff Comments.</p><p>None.</p>
        <p>Parts I and II</p><p>Item 7. Management's Discussion</p><p>Annual MD&amp;A.</p>'''
        sections = sec.extract_sections(html, '10-K')
        self.assertIn('shareholders and employees', sections[0].text)
        self.assertNotIn('Part I', sections[0].text)
        self.assertEqual(sections[-1].part, 'II')

    def test_descriptive_quarterly_mdna_precedes_financial_statements(self):
        html = '''<p>Part I – Financial information</p>
        <p>The following is Management’s discussion and analysis of the financial condition
        and results of operations for this quarter.</p><p>Substantive quarterly analysis.</p>
        <p>Consolidated statements of income (unaudited)</p>
        <table><tr><td>Sales</td><td>100</td><td>90</td></tr>
        <tr><td>Income</td><td>20</td><td>10</td></tr></table>
        <p>Notes to financial statements.</p><p>Item 3. Market Risk.</p><p>Market risk body.</p>'''
        sections = sec.extract_sections(html, '10-Q')
        by_key = {(s.part, s.item): s for s in sections}
        self.assertIn('Substantive quarterly analysis', by_key[('I', '2')].text)
        self.assertNotIn('Consolidated statements', by_key[('I', '2')].text)
        self.assertIn('Notes to financial statements', by_key[('I', '1')].text)
        self.assertTrue(by_key[('I', '2')].note)

    def test_numbered_mdna_intro_uses_existing_item_end(self):
        html = '''<p>Part I</p><p>Item 2. Management's Discussion and Analysis.</p>
        <p>The following is Management’s discussion and analysis of our quarterly results.</p>
        <p>Actual analysis.</p><p>Item 3. Market Risk.</p><p>Separate market-risk disclosure.</p>'''
        sections = sec.extract_sections(html, '10-Q')
        mdna = next(s for s in sections if s.item == '2')
        self.assertIn('Actual analysis', mdna.text)
        self.assertNotIn('Separate market-risk', mdna.text)
        self.assertFalse(mdna.note)

    def test_annual_mdna_reference_includes_body_later_in_document(self):
        html = '''<p>Item 7. Management’s Discussion and Analysis.</p>
        <p>Management’s discussion and analysis appears on pages 40–100.</p>
        <p>Item 8. Financial Statements.</p><p>Statements appear later.</p>
        <p>Item 15. Exhibits.</p><p>Exhibit list.</p>
        <p>The following is Management’s discussion and analysis of the financial condition
        and results of operations.</p><p>Actual substantive annual analysis.</p>
        <p>Management’s report on internal control over financial reporting</p>
        <p>Auditor report outside MD&amp;A.</p>'''
        sections = sec.extract_sections(html, '10-K')
        mdna = next(s for s in sections if s.item == '7')
        self.assertEqual(mdna.part, 'II')
        self.assertIn('pages 40–100', mdna.text)
        self.assertIn('Actual substantive annual analysis', mdna.text)
        self.assertNotIn('Auditor report', mdna.text)
        self.assertNotIn('Actual substantive annual analysis', next(s for s in sections if s.item == '15').text)

    def test_navigation_and_repeated_company_footers_are_removed(self):
        html = '''<p>Item 1. Statements.</p><p>Example &amp; Co.</p><p>Some narrative.</p>
        <p>Table of Contents</p><p>Example &amp; Co./2025 Form 10-K</p>
        <p>Example &amp; Co.</p><p>Example &amp; Co.</p><p>End narrative.</p>'''
        text = sec.extract_sections(html)[0].text
        self.assertNotIn('Table of Contents', text)
        self.assertNotIn('Example', text)
        self.assertIn('End narrative', text)

    def test_manifest_cli_and_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'filing.htm').write_text('<p>Item 1. Statements</p><p>Text, with commas.</p>')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'status': 'complete', 'filings': [
                {'file': 'filing.htm', 'form': '10-Q'}]}))
            output = root / 'parsed'
            with contextlib.redirect_stdout(io.StringIO()):
                code = sec.main([str(manifest), '--output', str(output)])
            self.assertEqual(code, 0)
            with (output / 'filing.htm_filing_sections.csv').open(newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]['Text'], 'Item 1. Statements\nText, with commas.')
            self.assertTrue((output / 'filing.htm_filing_tables.csv').exists())
            manifest.write_text(json.dumps({'status': 'failed'}))
            with self.assertRaisesRegex(ValueError, 'not complete'):
                list(sec.filing_inputs(manifest))

    def test_missing_headings_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'No item headings'):
            sec.extract_sections('<p>Unrecognized document</p>')

    def test_downloaded_quarterlies_when_available(self):
        paths = list((ROOT / 'data/sec').glob('**/*aapl-20260627.htm'))
        if not paths:
            self.skipTest('Local SEC downloads are optional')
        for path in paths:
            sections = sec.extract_sections(path.read_bytes(), '10-Q')
            self.assertEqual(len(sections), 11)
            financial = next(s for s in sections if (s.part, s.item) == ('I', '1'))
            risk = next(s for s in sections if (s.part, s.item) == ('II', '1A'))
            self.assertGreater(len(financial.tables), 0)
            self.assertGreater(len(risk.text), 1000)
            self.assertNotIn('109,417', financial.text)
            self.assertIn('109,417', '\n'.join(financial.tables))


if __name__ == '__main__':
    unittest.main()
