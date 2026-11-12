# COSC760FinancialRiskUsingNLP

Determining financial risk using NLP on EDGAR documents.

## Step 1: Acquire SEC filings

Requires Python 3.10 or newer. The acquisition command uses only Python's standard
library; no installation or API key is needed. Run commands from the repository root.

Set a User-Agent identifying this project and your real contact email:

```bash
export SEC_USER_AGENT="BDA SEC NLP Research your.name@example.com"
python3 scripts/acquire_sec.py --ticker AAPL --forms 10-K --limit-per-form 1
```

Replace the example contact before running. You can pass `--user-agent` instead of
setting the environment variable. The original `test.py` is an independent prototype;
it is not required by the acquisition command.

Use a CIK if you already know the company's SEC identifier, or if ticker lookup
is unavailable. Ticker lookup is an exact, case-insensitive match against the SEC's
current ticker list; company names and historical ticker symbols are not resolved.

```bash
python3 scripts/acquire_sec.py --cik 320193 --forms 10-K 10-Q --limit-per-form 3
```

Without `--forms`, the command requests the latest one each of 10-K, 10-Q and 8-K.
Forms match exactly: add `10-K/A` explicitly if amendments are needed. A limit is
an upper bound per form; shortfalls appear as warnings in the manifest and console.

### Historical filings and explicit accessions

By default, selection searches only the recent submissions block. For older
filings, add `--include-history`; this fetches the SEC's additional submissions
blocks, skipping blocks outside the requested filing-date range.

```bash
python3 scripts/acquire_sec.py --ticker AAPL --forms 10-K \
  --from-date 2020-01-01 --as-of 2025-12-31 \
  --include-history --limit-per-form 6
```

Dates are inclusive **filing dates**, not report-period dates. An as-of cutoff
filters today's filing metadata; it is not a historical snapshot of that metadata.
The command selects the most recent matching filings within the range, up to the
limit. Set the limit high enough for the desired corpus.

Use `--accession 0000320193-25-000079` for an exact filing; repeat the option for
multiple accessions. Accessions override forms and limits and cannot be combined
with date filters. Add `--include-history` if the accession is outside recent
submissions. An accession absent from searched history causes a failure.

### Saved data

Each run creates a separate folder under:

```text
data/sec/CIK<10-digit-CIK>/<UTC-timestamp>-<unique-id>/
  submissions_CIK<CIK>.json
  CIK<CIK>-submissions-001.json       # only when historical blocks are fetched
  <accession>_<primary-document>     # original primary filing bytes
  companyfacts_CIK<CIK>.json         # only with --companyfacts
  manifest.json
```

Use `--output /path/to/folder` to choose a different base directory. Generated SEC
data is ignored by Git. Repeated runs create new folders and download fresh copies.

The manifest records company identity, selection settings, original source URLs,
filing and report dates, byte counts and SHA-256 hashes. Its status is `complete`
only after all selected files and any requested companyfacts have been saved.
A failed or interrupted run retains completed files and a `failed` manifest with
an error and the planned accessions (when selection completed). A forcibly killed
process may leave `in_progress` or `.part` files. Treat only `complete` manifests
as ready for the next pipeline step. Restart with the same arguments to create a
new run; partial runs are not automatically resumed.

Add `--companyfacts` to save the SEC's aggregated XBRL facts separately. It is
optional because acquiring filing text does not require structured financial facts.

This step downloads primary HTML, text or XML documents with simple filenames.
It does not download exhibits, attachments, PDFs or transformed document paths,
extract clean text, or classify risk. In particular, an 8-K's earnings release may
be in Exhibit 99.1 and is not acquired here. Original markup is preserved for a
subsequent text-extraction step.

### SEC access and failures

The client spaces requests at 0.2 seconds by default (at most five per second for
one process), sends the supplied User-Agent, and retries temporary network errors
and HTTP 429/5xx responses up to four attempts. HTTP 403 stops immediately; check
your contact identity and retry later. Do not run concurrent collectors that
collectively exceed the SEC's rate limit. Use `--min-interval 0.5` to slow requests.
Ticker lookup and filing downloads use `www.sec.gov`, which can reject requests
even when `data.sec.gov` metadata works. Using a CIK avoids ticker lookup but does
not bypass archive access restrictions.

The manifest path is printed even when a run fails after its output folder was
created. Validation and ticker-lookup failures happen before a run folder exists.

References: [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
and [SEC access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

## Step 2: Extract section narrative and separate tables

Install the parser dependency into your Python environment:

```bash
python3 -m pip install -r requirements.txt
```

Pass a completed acquisition manifest to [parse_sections.py](./scripts/parse_sections.py):

```bash
python3 scripts/parse_sections.py \
  data/sec/CIK0000320193/20261016T011541950228Z-0d7286e3/manifest.json \
  --form 10-Q --output data/parsed
```

Replace the manifest path with the one printed by your acquisition run. Omit
`--form` to process supported forms in the manifest. To parse one HTML filing,
pass its path instead and specify `--form 10-Q`, `10-K`, or `8-K`. Amendments
are also accepted. Plain-text filings are not currently supported.

Each filing produces two UTF-8 CSVs in the output directory:

- `<filing>_filing_sections.csv`: section narrative, with source filename, form,
  part, item number, heading or descriptive label, count of separated tables,
  and an `ExtractionNote` when an alternate source layout was used.
- `<filing>_filing_tables.csv`: the separated table text, associated with its
  section. Rows are retained as lines and cells are separated with ` | `.

The parser identifies headings by item number and part, allowing title changes
between years. It retains paragraph boundaries and visible inline XBRL text,
removes hidden XBRL metadata and linked contents tables, and stops at signatures.
It preserves prose in layout tables while separating numeric grids and other
multi-column data tables. Add `--include-tables` to also include table text in
the section CSV. Original downloaded HTML remains unchanged.

Repeated part labels on page headers do not end a section. For annual filings,
item numbers determine the part even when page headers combine parts. Where
MD&A begins with its descriptive introductory paragraph, the parser can recover
that body up to the financial-report boundary. An annual Item 7 pointer to MD&A
later in the same HTML is retained alongside the recovered body. Short quarterly
risk disclosures that refer to an annual report stay as their original text.

Table classification and heading detection are heuristics. Review unfamiliar
issuers and layouts before using their text in an analysis: unusual prose tables
can be classified as data, unusual data tables can remain in the narrative, and
heading-like references can be mistaken for section boundaries. Missing items
are omitted rather than filled with artificial text. Cover pages and text before
the first item are excluded. The table CSV preserves removed table text within
detected sections; it does not preserve original table formatting. An 8-K can
only include the primary document downloaded by the acquisition script, not
unacquired exhibits.

### Compare the validation sample with source HTML

[validate_sections.py](./scripts/validate_sections.py) independently compares
Risk Factors and MD&A with source HTML blocks and verifies the source checksums:

```bash
python3 scripts/validate_sections.py \
  --input data/sec/validation \
  --parsed data/parsed/validation \
  --output data/parsed/validation/comparison.json \
  --report docs/sec_section_validation.md
```

It checks source prose retention, part/item metadata, and whether numeric table
rows appear in the narrative or companion tables. It returns a failing exit code
when these checks find discrepancies. The audit targets the sample's layouts;
its word-count and numeric-row criteria do not certify every short bullet,
heading, footnote, table format, or other filing section. See the
[2026-11-08 comparison](./docs/sec_section_validation_2026-11-08.md) for per-accession
results and the exact audit limits.

## Verification

The offline test suite checks acquisition, section boundaries, title variations,
table separation, layout-table prose, hidden metadata, CSV output, and optional
integration checks against locally downloaded filings:

```bash
python3 -m unittest discover -s tests -v
```
