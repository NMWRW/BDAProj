"""Check that the independent audit detects omissions and table leakage."""
import csv
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('validate_sec', ROOT / 'scripts/validate_sections.py')
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)

RISK = 'The company faces material risks from changing economic and market conditions.'
MDNA = 'Operating performance improved because customers increased purchases across all business segments.'
MARKUP = f'''<p>Item 1A. Risk Factors.</p><p>{RISK}</p>
<p>Item 1B. Staff Comments.</p><p>None.</p>
<p>Item 7. Management's Discussion and Analysis.</p><p>{MDNA}</p>
<table><tr><td>Total operating revenue</td><td>10,000</td><td>9,000</td></tr>
<tr><td>Total operating expense</td><td>5,000</td><td>4,000</td></tr></table>
<p>Item 7A. Market Risk.</p><p>Market disclosure.</p>'''


class AuditTests(unittest.TestCase):
    def compare(self, directory, omit_risk=False, leak=False):
        root = Path(directory)
        path = root / 'filing.htm'
        path.write_text(MARKUP, encoding='utf-8')
        filing = {'file': path.name, 'form': '10-K', 'accessionNumber': 'example',
                  'reportDate': '2025-12-31', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        table = 'Total operating revenue | 10,000 | 9,000\nTotal operating expense | 5,000 | 4,000'
        with (root / 'filing.htm_filing_sections.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['Part', 'Item', 'Text', 'TableCount'])
            writer.writeheader()
            writer.writerow({'Part': 'I', 'Item': '1A', 'Text': '' if omit_risk else RISK, 'TableCount': 0})
            writer.writerow({'Part': 'II', 'Item': '7', 'Text': MDNA + ('\n' + table if leak else ''), 'TableCount': 1})
        with (root / 'filing.htm_filing_tables.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['Part', 'Item', 'Text'])
            writer.writeheader()
            writer.writerow({'Part': 'II', 'Item': '7', 'Text': table})
        return audit.compare_filing(root / 'manifest.json', {'company': 'Example'}, filing, root)

    def test_complete_text_and_separate_tables_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            risk, mdna = self.compare(directory)
        self.assertEqual(risk['paragraphs_retained'], risk['paragraphs_checked'])
        self.assertEqual(mdna['numeric_rows_checked'], 2)
        self.assertEqual(mdna['numeric_rows_leaked'], 0)
        self.assertEqual(mdna['numeric_rows_unaccounted'], 0)

    def test_omitted_source_paragraph_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            risk, _ = self.compare(directory, omit_risk=True)
        self.assertEqual(risk['missing_paragraphs'], [RISK])

    def test_financial_rows_in_narrative_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            _, mdna = self.compare(directory, leak=True)
        self.assertEqual(mdna['numeric_rows_leaked'], 2)

    def test_unlinked_contents_number_does_not_replace_body_heading(self):
        markup = '''<table><tr><td>Item 1A.</td><td><a href="#risk">Risk Factors</a></td></tr>
        <tr><td>Item 2.</td><td>Sales</td></tr></table>
        <p id="risk">Item 1A. Risk Factors.</p><p>Actual body.</p>
        <p>Item 2. Unregistered Sales.</p>'''
        _, _, blocks = audit.source_blocks(markup)
        _, ranges = audit.reference_ranges(blocks, '10-Q', 'Risk Factors')
        self.assertFalse(blocks[ranges[0][0]].in_table)

    def test_quarterly_mdna_does_not_include_unregistered_sales(self):
        markup = '''<p>The following is Management’s discussion and analysis of operations.</p>
        <p>Actual MD&amp;A body.</p><p>Consolidated statements of income (unaudited)</p>
        <p>Financial statements.</p><p>Item 3. Market Risk.</p>
        <p>Item 1A. Risk Factors.</p><p>Risk body.</p>
        <p>Item 2. Unregistered Sales.</p><p>Refer to Capital Risk Management for repurchases.</p>
        <p>Item 3. Defaults.</p>'''
        _, _, blocks = audit.source_blocks(markup)
        _, ranges = audit.reference_ranges(blocks, '10-Q', 'MD&A')
        self.assertEqual(len(ranges), 1)
        self.assertTrue(blocks[ranges[0][0]].text.startswith('The following'))
        self.assertTrue(blocks[ranges[0][1]].text.startswith('Consolidated statements'))


if __name__ == '__main__':
    unittest.main()
