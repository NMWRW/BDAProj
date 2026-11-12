# SEC section validation — 2026-11-08

Comparison of Risk Factors and MD&A in 12 primary filings: three 10-Ks and three 10-Qs each from JPMorgan Chase and Walmart. The downloaded source bytes were checked against their acquisition-manifest SHA-256 hashes.

- Target sections located in output: 24/24.
- Source prose blocks retained: 7,914/7,914; 43 checked blocks are inside HTML tables.
- Numeric table-row signatures checked: 6,142; 0 found in narrative and 0 absent from both outputs.
- Correct part/item metadata: 24/24.

## Findings and fixes

1. **JPMorgan annual Risk Factors were truncated.** Repeated “Part I” running headers ended the section after its first page. Repeated part labels no longer end the current section.
2. **JPMorgan annual MD&A contained only a pointer.** The substantive MD&A is later in the same primary HTML. The parser now retains the original Item 7 pointer and incorporates that body, ending before management’s internal-control report.
3. **JPMorgan quarterly MD&A was absent.** Its body begins with “The following is Management’s discussion and analysis” rather than a numbered item heading. It is now assigned to Part I Item 2 and stops before the consolidated income statements. Those statements are assigned to Part I Item 1.
4. **JPMorgan annual parts were mislabeled.** Combined “Parts I and II” page labels do not establish the current item’s part. Annual item-number ranges now determine part metadata.
5. **Walmart retained its checked prose and separated numeric rows in the original output.** Standalone contents-navigation labels and recognizable repeated company/page footers have now been removed. Walmart legitimately omits Part II Items 3 and 4 in these quarterlies; nine detected sections is therefore not evidence of missing narrative.

## Per-section evidence

Character counts include headings and retained item pointers. “Prose” is the retained/checked source-block count; “grids” is the count of checked numeric row signatures. The JSON evidence also lists source ranges and any missing text.

| Accession / source | Form | Target | Before chars | After chars | Prose | Grids | Result |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| [0001628280-26-008131](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-008131_jpm-20251231.htm) | 10-K | Risk Factors | 5,213 | 111,967 | 510/510 | 0 | Pass checked criteria |
| [0001628280-26-008131](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-008131_jpm-20251231.htm) | 10-K | MD&A | 395 | 322,874 | 1049/1049 | 904 | Pass checked criteria |
| [0000019617-25-000270](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0000019617-25-000270_jpm-20241231.htm) | 10-K | Risk Factors | 10,164 | 135,513 | 559/559 | 0 | Pass checked criteria |
| [0000019617-25-000270](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0000019617-25-000270_jpm-20241231.htm) | 10-K | MD&A | 395 | 328,149 | 1070/1070 | 921 | Pass checked criteria |
| [0000019617-24-000225](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0000019617-24-000225_jpm-20231231.htm) | 10-K | Risk Factors | 5,835 | 131,368 | 542/542 | 0 | Pass checked criteria |
| [0000019617-24-000225](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0000019617-24-000225_jpm-20231231.htm) | 10-K | MD&A | 395 | 347,631 | 1160/1160 | 969 | Pass checked criteria |
| [0001628280-26-069523](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-069523_jpm-20260930.htm) | 10-Q | Risk Factors | 399 | 399 | 2/2 | 0 | Pass checked criteria |
| [0001628280-26-069523](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-069523_jpm-20260930.htm) | 10-Q | MD&A | 0 | 198,607 | 783/783 | 909 | Pass checked criteria |
| [0001628280-26-054343](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-054343_jpm-20260630.htm) | 10-Q | Risk Factors | 399 | 399 | 2/2 | 0 | Pass checked criteria |
| [0001628280-26-054343](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-054343_jpm-20260630.htm) | 10-Q | MD&A | 0 | 194,719 | 742/742 | 910 | Pass checked criteria |
| [0001628280-26-029344](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-029344_jpm-20260331.htm) | 10-Q | Risk Factors | 399 | 399 | 2/2 | 0 | Pass checked criteria |
| [0001628280-26-029344](../data/sec/validation/CIK0000019617/20261106T021300358749Z-c83b60a1/0001628280-26-029344_jpm-20260331.htm) | 10-Q | MD&A | 0 | 174,798 | 650/650 | 845 | Pass checked criteria |
| [0000104169-26-000055](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-26-000055_wmt-20260131.htm) | 10-K | Risk Factors | 93,133 | 93,133 | 112/112 | 0 | Pass checked criteria |
| [0000104169-26-000055](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-26-000055_wmt-20260131.htm) | 10-K | MD&A | 51,205 | 51,205 | 87/87 | 118 | Pass checked criteria |
| [0000104169-25-000021](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-25-000021_wmt-20250131.htm) | 10-K | Risk Factors | 97,453 | 97,453 | 118/118 | 0 | Pass checked criteria |
| [0000104169-25-000021](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-25-000021_wmt-20250131.htm) | 10-K | MD&A | 52,180 | 52,180 | 93/93 | 118 | Pass checked criteria |
| [0000104169-24-000056](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-24-000056_wmt-20240131.htm) | 10-K | Risk Factors | 88,629 | 88,629 | 107/107 | 0 | Pass checked criteria |
| [0000104169-24-000056](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-24-000056_wmt-20240131.htm) | 10-K | MD&A | 54,581 | 54,581 | 95/95 | 102 | Pass checked criteria |
| [0000104169-26-000154](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-26-000154_wmt-20260731.htm) | 10-Q | Risk Factors | 862 | 844 | 2/2 | 0 | Pass checked criteria |
| [0000104169-26-000154](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-26-000154_wmt-20260731.htm) | 10-Q | MD&A | 38,830 | 38,650 | 76/76 | 115 | Pass checked criteria |
| [0000104169-26-000102](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-26-000102_wmt-20260430.htm) | 10-Q | Risk Factors | 862 | 844 | 2/2 | 0 | Pass checked criteria |
| [0000104169-26-000102](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-26-000102_wmt-20260430.htm) | 10-Q | MD&A | 35,292 | 35,112 | 77/77 | 115 | Pass checked criteria |
| [0000104169-25-000191](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-25-000191_wmt-20251031.htm) | 10-Q | Risk Factors | 862 | 844 | 1/1 | 0 | Pass checked criteria |
| [0000104169-25-000191](../data/sec/validation/CIK0000104169/20261106T021321119384Z-38581c1b/0000104169-25-000191_wmt-20251031.htm) | 10-Q | MD&A | 37,645 | 37,465 | 73/73 | 116 | Pass checked criteria |

## Method and limits

The audit reconstructs leaf paragraphs/cells directly from the source HTML; it does not use the parser’s cleaned text or section-boundary functions. Explicit source item headings establish normal ranges. JPMorgan’s separate MD&A introduction and subsequent financial-report heading establish the additional range. An annual item pointer is checked as well as the full body.

Prose checks cover source blocks with at least eight whitespace-delimited words outside tables and at least 35 words inside tables. Case, whitespace and table-cell separators are ignored during matching. These are retention checks, not a claim that every short heading, bullet, or footnote was audited.

Numeric row checks use original table rows with at least two numeric cells, an alphabetic label, and a compact signature at least 20 characters long. Each signature is checked against both the target narrative and its table CSV. This does not detect every unusual table format, chart, or image.

Short quarterly Risk Factors sections in this sample refer readers to annual risk disclosures. They are valid short sections and are not filled with annual text. An ExtractionNote column identifies descriptive-heading and annual-reference recovery in regenerated narrative CSVs.

Results apply to the listed primary HTML filings and the two target sections. They do not certify all sections, other issuers, plain-text documents, unacquired exhibits, or real 8-K filings.
