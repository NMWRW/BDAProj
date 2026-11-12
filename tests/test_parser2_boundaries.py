"""Boundary-list regressions for the parser-2 experiment."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts import parse_sections as sec


class ConfirmedBoundaryTests(unittest.TestCase):
    def test_out_of_order_items_follow_document_positions(self):
        html = '''<h2>Item 1. Business</h2><p>Business body.</p>
        <h2>Item 2. Properties</h2><p>Property body.</p>
        <h2>Item 1A. Risk Factors</h2><p>Risk body.</p>'''
        sections = sec.extract_sections(html, '10-K')
        self.assertEqual([s.item for s in sections], ['1', '2', '1A'])
        self.assertNotIn('Property body', sections[0].text)
        self.assertNotIn('Risk body', sections[1].text)

    def test_unknown_item_is_not_an_accepted_boundary(self):
        html = '''<h2>Item 1. Business</h2><p>First business paragraph.</p>
        <p>Item 99. Example reference</p><p>Business narrative continues.</p>
        <h2>Item 2. Properties</h2><p>Property body.</p>'''
        sections = sec.extract_sections(html, '10-K')
        self.assertEqual([s.item for s in sections], ['1', '2'])
        self.assertIn('Business narrative continues', sections[0].text)

    def test_discarded_candidate_cannot_truncate_previous_section(self):
        html = '''<h2>Item 1. Business</h2><p>Business begins.</p>
        <p>Item 2. Properties</p><p>Business narrative continues after this misleading entry.</p>
        <h2>Item 2. Properties</h2><p>Actual property disclosure.</p>'''
        sections = sec.extract_sections(html, '10-K')
        self.assertIn('Business narrative continues', sections[0].text)
        self.assertIn('Actual property disclosure', sections[1].text)
        self.assertNotIn('Business narrative continues', sections[1].text)

    def test_repeated_continued_heading_preserves_both_pages(self):
        html = '''<h2>Item 1A. Risk Factors</h2><p>Opening risk paragraph.</p>
        <h2>Item 1A. Risk Factors (continued)</h2>
        <p>A much longer continuation of the risk discussion covers all the remaining
        operational, market and regulatory risks that the company faces.</p>
        <h2>Item 1B. Staff Comments</h2><p>None.</p>'''
        risk = sec.extract_sections(html, '10-K')[0]
        self.assertIn('Opening risk paragraph', risk.text)
        self.assertIn('much longer continuation', risk.text)
        self.assertNotIn('(continued)', risk.text)

    def test_grouped_items_have_one_physical_span(self):
        html = '''<h2>Items 1 and 2. Business and Properties</h2>
        <p>Shared disclosure for the two items.</p>
        <h2>Item 1A. Risk Factors</h2><p>Risk body.</p>'''
        sections = sec.extract_sections(html, '10-K')
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].items, ('1', '2'))
        self.assertEqual(sections[0].item, '1;2')
        self.assertIn('Shared disclosure', sections[0].text)
        self.assertNotIn('Risk body', sections[0].text)
        self.assertNotIn('Shared disclosure', sections[1].text)

    def test_8k_vocabulary_includes_static_pool_but_not_unknown_numbers(self):
        html = '''<h2>Item 6.06. Static Pool</h2><p>Static pool disclosure.</p>
        <p>Item 6.99. Unsupported reference</p><p>Static pool narrative continues.</p>
        <h2>Item 9.01. Financial Statements and Exhibits</h2><p>Exhibit text.</p>'''
        sections = sec.extract_sections(html, '8-K')
        self.assertEqual([s.item for s in sections], ['6.06', '9.01'])
        self.assertIn('Static pool narrative continues', sections[0].text)

    def test_short_cross_reference_does_not_create_section(self):
        html = '''<h2>Item 1. Business</h2><p>Business opening.</p>
        <p>Item 1A of this report discusses additional risks.</p>
        <p>Business discussion continues.</p><h2>Item 2. Properties</h2><p>Properties.</p>'''
        sections = sec.extract_sections(html, '10-K')
        self.assertEqual([s.item for s in sections], ['1', '2'])
        self.assertIn('Business discussion continues', sections[0].text)

    def test_grouped_cross_part_reference_remains_narrative(self):
        html = '''<h2>Item 1. Business</h2><p>Business opening.</p>
        <p>Items 1A and 7 of this report contain additional discussion.</p>
        <p>Business continues.</p><h2>Item 2. Properties</h2><p>Properties.</p>'''
        sections = sec.extract_sections(html, '10-K')
        self.assertEqual([s.item for s in sections], ['1', '2'])
        self.assertIn('Business continues', sections[0].text)

    def test_range_reference_is_text_but_ambiguous_range_heading_needs_review(self):
        html = '''<h2>Item 1. Business</h2>
        <p>Items 1 through 3 of this report provide further details.</p>
        <p>Business continues.</p><h2>Item 2. Properties</h2><p>Property body.</p>'''
        self.assertIn('Business continues', sec.extract_sections(html, '10-K')[0].text)
        with self.assertRaisesRegex(ValueError, 'Ranged item heading'):
            sec.extract_sections('<h2>Items 1–3. Combined disclosure</h2><p>Body.</p>', '10-K')

    def test_overlapping_group_and_individual_heading_needs_review(self):
        html = '''<h2>Items 1 and 2. Combined disclosure</h2><p>Shared body.</p>
        <h2>Item 2. Properties</h2><p>Separate property body.</p>'''
        with self.assertRaisesRegex(ValueError, 'Overlapping grouped'):
            sec.extract_sections(html, '10-K')

    def test_quarterly_vocabulary_is_part_specific(self):
        html = '''<h2>PART I</h2><h2>Item 1. Statements</h2><p>Financial narrative.</p>
        <p>Item 1A. Risk Factors</p><p>Additional financial narrative.</p>
        <h2>PART II</h2><h2>Item 1A. Risk Factors</h2><p>Actual risk narrative.</p>'''
        sections = sec.extract_sections(html, '10-Q')
        self.assertEqual([(s.part, s.item) for s in sections], [('I', '1'), ('II', '1A')])
        self.assertIn('Additional financial narrative', sections[0].text)

    def test_group_members_and_ranges_are_written_to_csv(self):
        html = '<h2>Items 1, 2 and 3. Combined disclosure</h2><p>Shared body.</p>'
        sections = sec.extract_sections(html, '10-K')
        with tempfile.TemporaryDirectory() as directory:
            paths = sec.write_sections(Path('filing.htm'), '10-K', sections, Path(directory))
            with paths[0].open(newline='') as handle:
                row = next(csv.DictReader(handle))
        self.assertEqual(json.loads(row['ItemMembers']), ['1', '2', '3'])
        self.assertEqual(len(json.loads(row['BlockSpans'])), 1)

    def test_contents_anchor_is_resolved_before_removing_contents(self):
        html = '''<table><tr><td><a href="#business">Item 1. Business</a></td></tr>
        <tr><td><a href="#properties">Item 2. Properties</a></td></tr></table>
        <div id="business"></div><p>Item 1. Business</p><p>Business body.</p>
        <div id="properties"></div><p>Item 2. Properties</p><p>Property body.</p>'''
        index = sec.build_section_index(html, '10-K')
        self.assertEqual(index.anchor_positions['business'], (0,))
        self.assertIn('contents_anchor', index.starts[0].evidence)
        self.assertEqual([c.items for c in index.starts], [('1',), ('2',)])

    def test_only_chosen_starts_and_explicit_stops_bound_intervals(self):
        html = '''<h2>Item 1. Business</h2><p>Body.</p><p>Item 2. Properties</p>
        <p>Business continuation.</p><h2>Item 2. Properties</h2><p>Properties.</p>
        <p>SIGNATURES</p><p>Signature text.</p>'''
        index = sec.build_section_index(html, '10-K')
        sections = sec.extract_sections(html, '10-K', section_index=index)
        self.assertEqual(sections[0].spans[0][1], index.starts[1].start)
        self.assertNotIn('Signature text', sections[-1].text)
        self.assertTrue(index.rejected)

    def test_resolved_mdna_pointer_is_not_absorbed_by_previous_item(self):
        html = '''<h2>Item 6. Reserved</h2><p>None.</p>
        <h2>Item 7. Management's Discussion and Analysis</h2>
        <p>Management's discussion and analysis appears on pages 40–100.</p>
        <h2>Item 8. Financial Statements</h2><p>Statements appear later.</p>
        <h2>Item 15. Exhibits</h2><p>Exhibit list.</p>
        <p>The following is Management’s discussion and analysis of operations.</p>
        <p>Substantive annual analysis.</p>
        <p>Management’s report on internal control over financial reporting</p>'''
        sections = sec.extract_sections(html, '10-K')
        six = next(s for s in sections if s.item == '6')
        mdna = next(s for s in sections if s.item == '7')
        self.assertNotIn('pages 40–100', six.text)
        self.assertIn('pages 40–100', mdna.text)
        self.assertEqual(six.spans[0][1], mdna.spans[0][0])

    def test_local_trial_index_ranges_have_unique_ownership_when_available(self):
        root = Path(__file__).resolve().parents[1]
        paths = list((root / 'data/parsed/parser-2/validation').glob('*_section_index.json'))
        if not paths:
            self.skipTest('Local parser-2 trial outputs are optional')
        for path in paths:
            with self.subTest(file=path.name):
                index = json.loads(path.read_text())
                spans, identities = [], set()
                for section in index['sections']:
                    for item in section['items']:
                        key = (section['part'], item)
                        self.assertNotIn(key, identities)
                        identities.add(key)
                    for start, stop in section['spans']:
                        self.assertLess(start, stop)
                        self.assertGreaterEqual(start, 0)
                        self.assertLessEqual(stop, index['block_count'])
                        spans.append((start, stop))
                ordered = sorted(spans)
                for previous, following in zip(ordered, ordered[1:]):
                    self.assertLessEqual(previous[1], following[0])


if __name__ == '__main__':
    unittest.main()
