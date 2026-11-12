"""Compare extracted Risk Factors and MD&A with local primary filing HTML.

This audit builds its source ranges directly from HTML blocks, independently of
parse_sections.py. It checks paragraph retention and numeric table-row separation.
It is a focused audit for the validation sample, not proof for every SEC layout.
"""

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup


LEAF_BLOCKS = ['div', 'p', 'h1', 'h2', 'h3', 'h4', 'li', 'td', 'th']
ITEM = re.compile(r'^Item\s+(\d+[A-C]?)\b[.\s:–—-]*(.*)$', re.I)
INTRO = re.compile(r'^The following is Management[’\']s discussion and analysis\b', re.I)
NUMBER = re.compile(r'^[($€£+−–—\-\s]*\d[\d,.\s]*[%)]*$|^[—–-]$')


def normalize(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip()


def compact(value):
    # Compare content independent of whitespace and CSV table-cell separators.
    return re.sub(r'[\s|]+', '', unicodedata.normalize('NFKC', value)).casefold()


@dataclass
class SourceBlock:
    position: int
    text: str
    in_table: bool
    linked: bool


def source_blocks(markup):
    soup = BeautifulSoup(markup, 'html.parser')
    for tag in list(soup.find_all(True)):
        if tag.parent is None:
            continue
        style = re.sub(r'\s+', '', str(tag.get('style', '')).lower())
        if (tag.name in {'head', 'script', 'style', 'noscript', 'ix:header', 'ix:hidden',
                         'xbrli:context', 'xbrli:unit', 'context', 'unit'}
                or tag.has_attr('hidden') or tag.get('aria-hidden') == 'true'
                or 'display:none' in style or 'visibility:hidden' in style):
            tag.decompose()
    # A source block is a leaf paragraph/cell, not text returned by the parser.
    elements = soup.find_all(True)
    positions = {id(tag): position for position, tag in enumerate(elements)}
    blocks = []
    for tag in soup.find_all(LEAF_BLOCKS):
        if tag.find(LEAF_BLOCKS) is not None:
            continue
        text = normalize(tag.get_text(' ', strip=True))
        if text:
            blocks.append(SourceBlock(positions[id(tag)], text,
                                      tag.find_parent('table') is not None,
                                      tag.find('a', href=True) is not None))
    return soup, positions, blocks


def reference_ranges(blocks, form, kind):
    """Locate source items, including MD&A presented elsewhere in the HTML."""
    item = '1A' if kind == 'Risk Factors' else ('7' if form == '10-K' else '2')
    headings = [(i, ITEM.match(block.text)) for i, block in enumerate(blocks)
                if not block.linked and len(block.text) <= 240 and ITEM.match(block.text)]
    starts = []
    for i, match in headings:
        if match[1].upper() != item:
            continue
        if kind == 'MD&A':
            title = match[2].strip()
            if not title and i + 1 < len(blocks) and not blocks[i + 1].linked:
                title = blocks[i + 1].text
            # Part II Item 2 may mention Capital Risk Management in its body;
            # that does not make it the Part I MD&A heading.
            if not re.match(r"Management[’']s\s+Discussion", title, re.I):
                continue
        starts.append(i)
    ranges = []
    if starts:
        # Some contents tables leave the item number unlinked. Prefer a body
        # heading outside a table; otherwise use the later layout-table heading.
        outside_tables = [i for i in starts if not blocks[i].in_table]
        start = outside_tables[0] if outside_tables else starts[-1]
        end = next((i for i, _ in headings if i > start), len(blocks))
        ranges.append((start, end))
    if kind == 'MD&A':
        intros = [i for i, block in enumerate(blocks)
                  if not block.in_table and INTRO.match(block.text)]
        if intros:
            start = intros[0]
            stop = (r'^Management[’\']s report on internal control over financial reporting'
                    if form == '10-K' else r'^Consolidated statements of income\b')
            end = next((i for i in range(start + 1, len(blocks))
                        if re.match(stop, blocks[i].text, re.I)), None)
            if end is None:
                raise ValueError('Cannot independently locate the end of descriptive MD&A')
            # Annual Item 7 contains a pointer to this additional source span.
            if not any(a <= start < b for a, b in ranges):
                ranges.append((start, end))
    if not ranges:
        raise ValueError(f'Cannot locate source {kind}')
    return item, ranges


def compare_filing(manifest_path, manifest, filing, parsed):
    path = manifest_path.parent / filing['file']
    markup = path.read_bytes()
    if hashlib.sha256(markup).hexdigest() != filing['sha256']:
        raise ValueError(f'Source checksum differs from manifest: {path}')
    # Complete annual sections exceed csv's default 128 KiB per-field limit.
    # The source document size is a natural upper bound for this local audit.
    csv.field_size_limit(max(csv.field_size_limit(), len(markup)))
    soup, positions, blocks = source_blocks(markup)
    section_path = parsed / (path.name + '_filing_sections.csv')
    table_path = parsed / (path.name + '_filing_tables.csv')
    with section_path.open(newline='', encoding='utf-8') as handle:
        sections = list(csv.DictReader(handle))
    with table_path.open(newline='', encoding='utf-8') as handle:
        table_records = list(csv.DictReader(handle))
    results = []
    for kind in ('Risk Factors', 'MD&A'):
        item, ranges = reference_ranges(blocks, filing['form'], kind)
        part = ('I' if kind == 'Risk Factors' else 'II') if filing['form'] == '10-K' else (
            'II' if kind == 'Risk Factors' else 'I')
        # Find the old annual output even if its part label is wrong, so the
        # before/after report distinguishes lost content from wrong metadata.
        matches = [s for s in sections if s['Item'] == item
                   and (filing['form'] == '10-K' or s['Part'] == part)]
        record = matches[0] if len(matches) == 1 else None
        text = record['Text'] if record else ''
        prose = compact(text)
        table_text = compact('\n'.join(r['Text'] for r in table_records
                                      if r['Item'] == item and (record is None or r['Part'] == record['Part'])))
        candidates = [block for start, end in ranges for block in blocks[start + 1:end]
                      if (not block.in_table and len(block.text.split()) >= 8)
                      or (block.in_table and len(block.text.split()) >= 35)]
        # Include the descriptive introductory paragraph when it is the start.
        for start, _ in ranges:
            if INTRO.match(blocks[start].text):
                candidates.append(blocks[start])
        missing = [b for b in candidates if compact(b.text) not in prose]
        span_positions = [(blocks[start].position,
                           blocks[end].position if end < len(blocks) else len(positions))
                          for start, end in ranges]
        grid_rows = []
        for row in soup.find_all('tr'):
            position = positions[id(row)]
            if not any(a <= position < b for a, b in span_positions):
                continue
            cells = [normalize(c.get_text('', strip=False))
                     for c in row.find_all(['td', 'th'], recursive=False)]
            cells = [c for c in cells if c]
            value = compact(' '.join(cells))
            if (sum(bool(NUMBER.fullmatch(c)) for c in cells) >= 2
                    and re.search(r'[A-Za-z]{3}', value) and len(value) >= 20):
                grid_rows.append(value)
        leaked = [row for row in grid_rows if row in prose]
        unaccounted = [row for row in grid_rows if row not in prose and row not in table_text]
        results.append({
            'accession': filing['accessionNumber'], 'company': manifest['company'],
            'form': filing['form'], 'report_date': filing['reportDate'], 'section': kind,
            'part': part, 'item': item, 'present': record is not None,
            'part_correct': bool(record and record['Part'] == part), 'characters': len(text),
            'paragraphs_checked': len(candidates), 'paragraphs_retained': len(candidates) - len(missing),
            'missing_paragraphs': [b.text for b in missing],
            'layout_paragraphs_checked': sum(b.in_table for b in candidates),
            'numeric_rows_checked': len(grid_rows), 'numeric_rows_leaked': len(leaked),
            'numeric_rows_unaccounted': len(unaccounted),
            'leaked_row_samples': leaked[:3], 'unaccounted_row_samples': unaccounted[:3],
            'tables': int(record['TableCount']) if record else 0,
            'source_file': str(path), 'section_csv': str(section_path),
            'reference_ranges': [[blocks[a].text[:120], blocks[b].text[:120] if b < len(blocks) else 'EOF']
                                 for a, b in ranges],
        })
    return results


def write_report(path, results, baseline):
    """Write a readable comparison alongside the detailed local JSON evidence."""
    old = {(r['accession'], r['section']): r for r in baseline}
    checked = sum(r['paragraphs_checked'] for r in results)
    retained = sum(r['paragraphs_retained'] for r in results)
    grids = sum(r['numeric_rows_checked'] for r in results)
    leaked = sum(r['numeric_rows_leaked'] for r in results)
    unaccounted = sum(r['numeric_rows_unaccounted'] for r in results)
    layout = sum(r['layout_paragraphs_checked'] for r in results)
    lines = [
        '# SEC section validation — 2026-11-08', '',
        'Comparison of Risk Factors and MD&A in 12 primary filings: three 10-Ks and three '
        '10-Qs each from JPMorgan Chase and Walmart. The downloaded source bytes were '
        'checked against their acquisition-manifest SHA-256 hashes.', '',
        f'- Target sections located in output: {sum(r["present"] for r in results)}/{len(results)}.',
        f'- Source prose blocks retained: {retained:,}/{checked:,}; '
        f'{layout:,} checked blocks are inside HTML tables.',
        f'- Numeric table-row signatures checked: {grids:,}; '
        f'{leaked:,} found in narrative and {unaccounted:,} absent from both outputs.',
        f'- Correct part/item metadata: {sum(r["part_correct"] for r in results)}/{len(results)}.', '',
        '## Findings and fixes', '',
        '1. **JPMorgan annual Risk Factors were truncated.** Repeated “Part I” running '
        'headers ended the section after its first page. Repeated part labels no longer '
        'end the current section.',
        '2. **JPMorgan annual MD&A contained only a pointer.** The substantive MD&A is '
        'later in the same primary HTML. The parser now retains the original Item 7 '
        'pointer and incorporates that body, ending before management’s internal-control report.',
        '3. **JPMorgan quarterly MD&A was absent.** Its body begins with “The following '
        'is Management’s discussion and analysis” rather than a numbered item heading. '
        'It is now assigned to Part I Item 2 and stops before the consolidated income '
        'statements. Those statements are assigned to Part I Item 1.',
        '4. **JPMorgan annual parts were mislabeled.** Combined “Parts I and II” page '
        'labels do not establish the current item’s part. Annual item-number ranges '
        'now determine part metadata.',
        '5. **Walmart retained its checked prose and separated numeric rows in the '
        'original output.** Standalone contents-navigation labels and recognizable '
        'repeated company/page footers have now been removed. Walmart legitimately '
        'omits Part II Items 3 and 4 in these quarterlies; nine detected sections is '
        'therefore not evidence of missing narrative.', '',
        '## Per-section evidence', '',
        'Character counts include headings and retained item pointers. “Prose” is the '
        'retained/checked source-block count; “grids” is the count of checked numeric '
        'row signatures. The JSON evidence also lists source ranges and any missing text.', '',
        '| Accession / source | Form | Target | Before chars | After chars | Prose | Grids | Result |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | --- |',
    ]
    for r in results:
        previous = old.get((r['accession'], r['section']))
        before = f"{previous['characters']:,}" if previous else '—'
        problems = []
        if not r['present']:
            problems.append('missing section')
        if not r['part_correct']:
            problems.append('wrong part')
        if r['missing_paragraphs']:
            problems.append(f"{len(r['missing_paragraphs'])} omitted blocks")
        if r['numeric_rows_leaked']:
            problems.append(f"{r['numeric_rows_leaked']} leaked rows")
        if r['numeric_rows_unaccounted']:
            problems.append(f"{r['numeric_rows_unaccounted']} unaccounted rows")
        result = '; '.join(problems) if problems else 'Pass checked criteria'
        relative = Path(os.path.relpath(r['source_file'], path.parent)).as_posix()
        lines.append(f"| [{r['accession']}]({relative}) | {r['form']} | {r['section']} | "
                     f"{before} | {r['characters']:,} | {r['paragraphs_retained']}/"
                     f"{r['paragraphs_checked']} | {r['numeric_rows_checked']} | {result} |")
    lines.extend([
        '', '## Method and limits', '',
        'The audit reconstructs leaf paragraphs/cells directly from the source HTML; '
        'it does not use the parser’s cleaned text or section-boundary functions. '
        'Explicit source item headings establish normal ranges. JPMorgan’s separate '
        'MD&A introduction and subsequent financial-report heading establish the '
        'additional range. An annual item pointer is checked as well as the full body.', '',
        'Prose checks cover source blocks with at least eight whitespace-delimited '
        'words outside tables and at least 35 words inside tables. Case, whitespace '
        'and table-cell separators are ignored during matching. These are retention '
        'checks, not a claim that every short heading, bullet, or footnote was audited.', '',
        'Numeric row checks use original table rows with at least two numeric cells, '
        'an alphabetic label, and a compact signature at least 20 characters long. '
        'Each signature is checked against both the target narrative and its table '
        'CSV. This does not detect every unusual table format, chart, or image.', '',
        'Short quarterly Risk Factors sections in this sample refer readers to '
        'annual risk disclosures. They are valid short sections and are not filled '
        'with annual text. An ExtractionNote column identifies descriptive-heading '
        'and annual-reference recovery in regenerated narrative CSVs.', '',
        'Results apply to the listed primary HTML filings and the two target sections. '
        'They do not certify all sections, other issuers, plain-text documents, '
        'unacquired exhibits, or real 8-K filings.', '',
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/sec/validation'))
    parser.add_argument('--parsed', type=Path, default=Path('data/parsed/validation'))
    parser.add_argument('--output', type=Path, required=True, help='JSON evidence file')
    parser.add_argument('--baseline', type=Path, help='Earlier JSON evidence for before/after counts')
    parser.add_argument('--report', type=Path, help='Readable Markdown comparison')
    parser.add_argument('--show-missing', type=int, default=0, help='Print sample missing blocks per section')
    args = parser.parse_args(argv)
    results = []
    for path in sorted(args.input.glob('**/manifest.json')):
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('status') != 'complete':
            continue
        for filing in manifest['filings']:
            if filing['form'] in {'10-K', '10-Q'}:
                records = compare_filing(path, manifest, filing, args.parsed)
                results.extend(records)
                for r in records:
                    print(f"{r['accession']} {r['section']}: "
                          f"{r['paragraphs_retained']}/{r['paragraphs_checked']} paragraphs, "
                          f"{r['numeric_rows_leaked']} leaked/{r['numeric_rows_unaccounted']} unaccounted "
                          f"of {r['numeric_rows_checked']} numeric rows; part correct={r['part_correct']}")
                    for paragraph in r['missing_paragraphs'][:args.show_missing]:
                        print('  MISSING:', paragraph[:240])
                    if args.show_missing and r['unaccounted_row_samples']:
                        print('  UNACCOUNTED:', r['unaccounted_row_samples'][0][:200])
    if not results:
        raise ValueError('No validation filings found')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if args.report:
        baseline = json.loads(args.baseline.read_text(encoding='utf-8')) if args.baseline else []
        write_report(args.report, results, baseline)
    return int(any(not r['present'] or not r['part_correct'] or r['missing_paragraphs']
                   or r['numeric_rows_leaked'] or r['numeric_rows_unaccounted'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
