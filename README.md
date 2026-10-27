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

## Verification

The offline test suite checks selection, history pagination, ticker lookup,
validation, provenance hashes, retry handling, and partial-run failures:

```bash
python3 -m unittest discover -s tests -v
```
