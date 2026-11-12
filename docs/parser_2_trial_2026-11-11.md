# parser-2 boundary trial — 2026-11-11

The `parser-2` branch implements the proposed rule: select acceptable section
starts first, then extract to the next selected start in document order.
Existing uncommitted work was retained when the branch was created. The trial
changes remain uncommitted for review.

## Implementation

[The parser](../scripts/parse_sections.py) uses an ordered array of prepared text
blocks, an anchor-to-position hashmap, and candidate lists keyed by part/item
identities. It does not sort by item number or introduce a trained model.

1. Preserve contents-link targets and heading-format evidence while preparing HTML.
2. Check candidates against form/part item vocabularies based on the SEC
   [10-K](https://www.sec.gov/files/form10-k.pdf),
   [10-Q](https://www.sec.gov/files/form10-q.pdf), and
   [8-K](https://www.sec.gov/files/form8-k.pdf) definitions.
3. Select among duplicates using anchor evidence, presence of body content,
   formatting, and document position. Repeated continued headings do not replace
   a substantive opening heading.
4. Slice between the selected starts and explicit part, signature, or recovery
   stopping points. Discarded item candidates cannot truncate an accepted item.
5. Store an explicit grouped list such as `Items 1 and 2` once, with both member
   identities. Ambiguous ranges, cross-part groups, or overlapping group and
   individual ownership require review.

The original same-document MD&A recovery is retained. A confirmed annual Item 7
pointer remains its own recorded source span and an explicit stopping point for
the previous item, so resolving MD&A does not duplicate that pointer into Item 6.

## Verification

All **50 offline tests passed**, including 16 new boundary/index tests. Optional
integration checks parsed the locally downloaded filings and checked that all
12 trial section indexes have valid, non-overlapping spans and unique item ownership.

| Case | Result on parser-2 |
| --- | --- |
| `Item 1 → Item 2 → Item 1A` | Preserves source order and correct physical endings. |
| Unknown item number | Retains text and records a review warning; does not create a boundary. |
| Discarded duplicate before the real heading | Preserves the preceding section's continuation. |
| Repeated continued header | Retains both opening and continuation text. |
| `Items 1 and 2` or `Items 1, 2 and 3` | One physical span with explicit group membership. |
| Contents links and empty target anchors | Retains anchor evidence before removing contents. |
| Item-like references in prose | Keeps tested reference patterns in the narrative. |
| Ranges or overlapping group identities | Raises an explicit review error instead of assigning guessed membership. |
| Resolved annual MD&A pointer | Recorded in MD&A only, without spilling into the previous item. |

The independent source audit was rerun on the same 12 JPMorgan/Walmart filings
used in the [earlier comparison](sec_section_validation_2026-11-08.md):

| Sample | Filings | Target sections | Eligible prose retained | Numeric row signatures accounted for |
| --- | ---: | ---: | ---: | ---: |
| JPMorgan 10-K | 3 | 6 | 4,890/4,890 | 2,794/2,794 |
| JPMorgan 10-Q | 3 | 6 | 2,181/2,181 | 2,664/2,664 |
| Walmart 10-K | 3 | 6 | 612/612 | 338/338 |
| Walmart 10-Q | 3 | 6 | 231/231 | 346/346 |
| Total | 12 | 24 | 7,914/7,914 | 6,142/6,142 |

All 24 target sections had correct part/item metadata. There were zero detected
numeric-row leaks and zero checked rows absent from both outputs. These source
audit metrics match the previous parser's passing results on this sample.

## Outputs and reproduction

Trial CSV pairs and section-index JSON files are under
[data/parsed/parser-2/validation/](../data/parsed/parser-2/validation/), alongside
[comparison evidence](../data/parsed/parser-2/validation/comparison.json).
Earlier validation output was preserved.

The narrative CSV adds `ItemMembers`, `BlockSpans`, and `BoundaryEvidence`.
`ItemMembers` lists aliases; for a group, `Item` contains a semicolon-separated
label such as `1;2`. Keep the shared text once when assembling an NLP dataset.
`BlockSpans` uses half-open ranges in prepared text blocks, not original HTML
byte offsets. Referenced MD&A can have two disjoint ranges.

```bash
.venv/bin/python -m unittest discover -s tests -v

for manifest in data/sec/validation/CIK*/*/manifest.json; do
  .venv/bin/python scripts/parse_sections.py "$manifest" \
    --output data/parsed/parser-2/validation || break
done

.venv/bin/python scripts/validate_sections.py \
  --parsed data/parsed/parser-2/validation \
  --output data/parsed/parser-2/validation/comparison.json
```

## Remaining limits

This is a rule-based boundary-selection experiment, not a universal correctness
guarantee. It does not implement the proposed lossless raw-source ledger or a
candidate graph. Existing metadata, page-furniture and table cleanup still runs.
The diagnostics describe selected boundaries in the prepared stream.

The source audit still checks prose blocks of at least eight words outside
tables and 35 words inside tables, plus its defined numeric-row signatures.
It does not certify every short bullet/footnote, other sections, unseen issuers,
or grouped disclosures. Grouped-heading behavior has synthetic regression tests;
the 12-file source audit is not an independent grouped-layout benchmark. Modern
8-K item membership is tested synthetically; legacy integer-numbered 8-K forms
and other document formats need separate support. The item vocabulary also
needs review if the SEC forms change.

Next: independently annotate unseen issuer layouts, evaluate false acceptance
and review rates, and add full source-block accounting before broad deployment.
