# Project changelog

See [AGENTS.md](AGENTS.md) for the project goal, pipeline, and working conventions.

## 2026-11-08 — Cross-issuer section comparison

### Current work

- **Compared 12 additional primary filings.** The sample contains three 10-Ks and three 10-Qs each from JPMorgan Chase and Walmart. The [comparison report](docs/sec_section_validation_2026-11-08.md) records all 24 Risk Factors/MD&A checks by accession and links to each local source. Original filing hashes match the acquisition manifests.
- **Fixed confirmed JPMorgan extraction failures.** Repeated part page labels no longer truncate Risk Factors. Annual items get the correct part even with combined page labels. MD&A bodies presented outside a numbered item are recovered from their introductory paragraph up to the financial-report boundary; an annual Item 7 pointer is retained with the substantive body. An `ExtractionNote` records this recovery in narrative CSVs.
- **Regenerated all 12 validation CSV pairs.** Walmart's checked prose and numeric rows already passed the source comparison; navigation labels and recognized page/company footers were cleaned up. Its omitted quarterly Part II Items 3/4 are legitimate, so tests no longer impose Apple's section count on every issuer.
- **Independent source checks passed.** All 24 target sections have correct part/item metadata. All 7,914 eligible source prose blocks are retained, including 43 inside layout tables. All 6,142 checked numeric row signatures are accounted for in companion tables, with none found in the narrative. These criteria do not certify short bullets/footnotes, every table format, or sections outside Risk Factors and MD&A.
- **Repeatable audit.** [validate_sections.py](scripts/validate_sections.py) reads source HTML independently of the parser and emits local JSON evidence and a Markdown report. Detailed evidence is under the ignored validation output directory. All 34 offline tests passed. Parser/audit regression tests cover truncation, descriptive MD&A, annual references, contents entries, missing prose, and table leakage.

### Next step

Assemble a traceable section-level dataset for Risk Factors and MD&A from the validated narrative CSVs and acquisition manifests. Retain CIK, accession, company, form, filing date, report date, part, item, extraction note, and validation status. Deduplicate repeated acquisition runs by filing identity and part/item. Keep quarterly references to annual risk disclosures distinguishable from substantive new risk text. Validate additional layouts before treating them as ready, and separately review other filing sections if they enter the NLP dataset.

## 2026-11-05 — SEC filing section extraction

### Initial work

- **Directory organization.** Moved the parser into [scripts/](scripts/) alongside acquisition. Commands and test imports now use [scripts/parse_sections.py](scripts/parse_sections.py); run both pipeline scripts from the repository root.
- **Parser naming and readability.** Renamed the entry point to [parse_sections.py](scripts/parse_sections.py), updated documentation and the test import, and added comments explaining HTML cleanup, table heuristics, section boundaries, manifest inputs, and CSV outputs. Parsing behavior is unchanged.
- **Acquisition is in place.** The downloader saves original EDGAR primary documents and a run manifest. Local complete manifests contain Apple 10-Ks and 10-Qs.
- **Section extraction is the active stage.** The parser reads those manifests, finds sections by part and item, and separates financial tables from narrative by default. Removed table text within detected sections is available in the companion CSV. The parser, its tests, and related documentation are in the local working tree.
- **Verified locally.** All 24 tests passed. The saved output contains ten Apple 10-Ks and ten Apple 10-Qs, with all 11 expected sections present in each of the ten 10-Qs. The suite exercises 8-K section detection with synthetic markup. Real-filing validation so far covers Apple 10-Ks and 10-Qs.
- **NLP analysis is pending.** The repository currently produces section text and separated tables. It has no risk scoring or modeling stage yet.

### Validation plan (completed 2026-11-08)

Validate the parser on a small set of 10-Ks and 10-Qs from at least two additional issuers before building the NLP dataset.

1. Acquire the sample, record the issuers and accessions, and parse each completed manifest with the default narrative settings.
2. Compare Risk Factors and Management's Discussion and Analysis with the original filings. Check that headings point to the body, sections end in the right place, layout-table prose survives, and financial grids appear in the companion table CSV.
3. Record any omissions, misplaced text, or table leakage by accession. Fix reproducible parsing issues and rerun the offline suite. Consider the sample ready when each target section matches the source where present and any exceptions are documented.

After validation, assemble a section-level NLP dataset from the narrative CSVs and acquisition manifests. Retain CIK, accession, form, filing date, report date, part, and item so each text record can be traced to the original filing.
