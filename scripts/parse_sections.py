"""Extract SEC filing sections with tabular data separate from narrative.

Accept a primary HTML filing or a completed acquire_sec.py manifest.
Original downloaded documents are never modified. No network access is used.

Run from the repository root: python3 scripts/parse_sections.py --help

Processing order:
1. Read the filing HTML and remove hidden metadata and contents tables.
2. Replace data tables with markers, keeping their text for a separate CSV.
3. Find part/item headings and collect the text belonging to each section.
4. Write one narrative CSV and one companion table CSV for each filing.
"""

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import unicodedata

from bs4 import BeautifulSoup, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction, Tag


# Match item numbers such as 1, 1A, or 2.02 at the start of a text block.
# Group 1 is the item number; group 2 is the remaining heading text. Matching
# the number rather than a fixed title allows heading wording to vary by year.
ITEM = re.compile(r"^Item\s+(\d{1,2}(?:\.\d{2})?[A-C]?)\b[.\s:–—-]*(.*)$", re.I)
# Longer Roman numerals come first so PART II is not mistaken for PART I.
PART = re.compile(r"^PART\s+(IV|III|II|I)\b", re.I)
# Identify cells containing only numeric data, currency/percent punctuation,
# or a dash used for an empty amount. Numbers within sentences do not match.
NUMBER = re.compile(r"^[($€£+−–—\-\s]*\d[\d,.\s]*[%)]*$|^[—–-]$")
# Markers preserve a removed table's position until we know its section.
TABLE_MARKER = re.compile(r"\[\[SEC_TABLE_(\d+)\]\]")
# These HTML elements start/end text blocks. Inline elements such as <span>
# stay within the same block so formatting does not split words or headings.
BLOCKS = {"p", "div", "section", "article", "h1", "h2", "h3", "h4",
          "h5", "h6", "tr", "li", "blockquote", "pre"}
SUPPORTED_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "8-K", "8-K/A"}
MDNA_INTRO = re.compile(r"^The following is Management[’']s discussion and analysis\b", re.I)
PART_LABEL = re.compile(r"^Parts?\s+(?:IV|III|II|I)(?:\s+and\s+(?:IV|III|II|I))?[.]?$", re.I)


def normalize(value):
    """Standardize Unicode and whitespace within one cell or text block."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


@dataclass
class Section:
    """One detected item and its narrative plus separately collected tables.

    The part/item pair identifies a section: a 10-Q has Item 1 in both parts.
    Title stores the heading or a descriptive label for an unnumbered section.
    Tables stores table text in body order; note explains alternate recovery.
    """

    part: str
    item: str
    title: str
    text: str
    tables: list
    note: str = ""


def table_rows(table):
    """Read cells belonging to this table without duplicating nested rows."""
    # HTML tables can contain other tables. Only use rows whose closest table
    # is this one, and only take the direct cells belonging to each row.
    return [[normalize(cell.get_text(" ", strip=True))
             for cell in row.find_all(["td", "th"], recursive=False)]
            for row in table.find_all("tr") if row.find_parent("table") is table]


def is_data_table(table):
    """Heuristic: separate grids while retaining prose and heading layouts.

    HTML has no dependable financial-table flag. The companion table CSV
    retains everything this heuristic removes for later inspection.
    """
    rows = [[cell for cell in row if cell] for row in table_rows(table)]
    rows = [row for row in rows if row]
    cells = [cell for row in rows for cell in row]
    if not cells:
        return False
    # Repeated columns with a meaningful share of numeric cells suggest a
    # financial grid. The 15% cutoff is a heuristic, not an SEC format rule.
    numeric = sum(bool(NUMBER.fullmatch(cell)) for cell in cells)
    multi_column = sum(len(row) >= 2 for row in rows)
    if multi_column >= 2 and numeric >= 2 and numeric / len(cells) >= 0.15:
        return True
    # If the numeric-grid test did not match, a cell with 35+ words suggests
    # prose arranged in a layout table. Keep that text in the narrative.
    if any(len(cell.split()) >= 35 for cell in cells):
        return False
    # Some headings use separate cells for "Item 1A." and "Risk Factors".
    if len(rows) <= 2 and all(ITEM.match(" ".join(row)) or
                              PART.match(" ".join(row)) for row in rows):
        return False
    # Short, repeated columns can also hold nonnumeric data, such as exhibits.
    return multi_column >= 2


def visible_blocks(markup):
    """Return (text blocks, table-id-to-text mapping) before section slicing."""
    soup = BeautifulSoup(markup, "html.parser")
    # XBRL metadata describes machine-readable facts and can duplicate visible
    # filing content. Remove hidden containers, but keep visible inline facts.
    # Make a list first because decompose() changes the tree while we visit it.
    for tag in list(soup.find_all(True)):
        if tag.parent is None:
            continue
        name = tag.name.lower()
        style = re.sub(r"\s+", "", str(tag.get("style", "")).lower())
        if (name in {"head", "script", "style", "noscript", "ix:hidden", "ix:header",
                     "xbrli:context", "xbrli:unit", "context", "unit"}
                or tag.has_attr("hidden") or tag.get("aria-hidden") == "true"
                or "display:none" in style or "visibility:hidden" in style):
            tag.decompose()

    # Contents entries repeat real headings. Internal links start with "#"
    # and usually point to body headings outside the contents table itself.
    for table in reversed(list(soup.find_all("table"))):
        if table.parent is None:
            continue
        links = [a for a in table.find_all("a", href=True)
                 if a["href"].startswith("#")]
        # A wrapper containing both contents and target headings is body layout.
        targets_inside = any(table.find(id=a["href"][1:]) is not None or
                             table.find(attrs={"name": a["href"][1:]}) is not None
                             for a in links if a["href"][1:])
        if (len(links) >= 2 and not targets_inside and table.find("table") is None
                and len(table.get_text()) < 6000
                and re.search(r"\bItem\s+\d", table.get_text(" "), re.I)):
            table.decompose()

    tables = {}
    # Process inner tables first so layout wrappers do not swallow their prose.
    for table in reversed(list(soup.find_all("table"))):
        if table.parent is None:
            continue
        if table.find("table") is not None or TABLE_MARKER.search(table.get_text()):
            # Keep outer wrappers around nested tables/markers; otherwise their
            # surrounding prose or an already extracted table could be lost.
            continue
        if is_data_table(table):
            number = len(tables) + 1
            tables[number] = "\n".join(" | ".join(row) for row in table_rows(table))
            # A block element ensures the marker gets its own text line.
            marker = soup.new_tag("div")
            marker.string = f"[[SEC_TABLE_{number}]]"
            table.replace_with(marker)

    pieces = []

    def walk(node):
        """Walk HTML in document order, preserving paragraph/heading breaks."""
        if isinstance(node, (Comment, Declaration, Doctype, ProcessingInstruction)):
            return
        if isinstance(node, NavigableString):
            pieces.append(str(node))
        elif isinstance(node, Tag):
            block = node.name in BLOCKS
            if block or node.name in {"br", "hr"}:
                pieces.append("\n")
            for child in node.children:
                walk(child)
            if block:
                pieces.append("\n")
            elif node.name in {"td", "th"}:
                # Layout-table cells still need a space between their text.
                pieces.append(" ")

    walk(soup.body or soup)
    lines = [normalize(line) for line in "".join(pieces).splitlines()]
    occurrences = Counter(lines)
    # Drop standalone page numbers and the footer style seen in Apple filings.
    # Paragraph boundaries survive because normalization happens line by line.
    lines = [line for line in lines if line and not re.fullmatch(r"\d+", line)
             and line.casefold() != "table of contents"
             and not re.search(r"(?:\||/).*Form\s+10-[KQ](?:\s*\|\s*\d+)?$", line, re.I)
             and not (occurrences[line] >= 3 and len(line) < 90
                      and re.fullmatch(r".+(?:Inc\.|Corp\.|& Co\.)", line))]
    return lines, tables


def item_part(form, item, current_part):
    """Resolve annual item parts even when pages say 'Parts I and II'."""
    if form.startswith("10-K"):
        number = int(re.match(r"\d+", item)[0])
        return "I" if number <= 4 else "II" if number <= 9 else "III" if number <= 14 else "IV"
    return current_part


def descriptive_mdna_span(lines, form, covered_ranges=()):
    """Locate MD&A presented as an introduction instead of a numbered item.

    Some primary filings put MD&A before financial statements or append it
    after a numbered Item 7 pointer. Require a recognizable end heading so
    financial statements and auditor reports do not enter MD&A by accident.
    """
    start = next((i for i, line in enumerate(lines) if MDNA_INTRO.match(line)), None)
    if start is None or not form.startswith(("10-K", "10-Q")):
        return None
    # An ordinary numbered MD&A item already supplies a reliable ending. Its
    # introduction need not be followed by any special financial-report label.
    if any(a <= start < b for a, b in covered_ranges):
        return None
    stop = (r"^Management[’']s report on internal control over financial reporting"
            if form.startswith("10-K") else r"^Consolidated statements of income\b")
    end = next((i for i in range(start + 1, len(lines))
                if re.match(stop, lines[i], re.I)), None)
    if end is None:
        raise ValueError("Found descriptive MD&A but could not identify its ending")
    return start, end


def extract_sections(markup, form="10-Q", include_tables=False):
    """Split cleaned HTML into Section objects, optionally reinserting tables."""
    if form not in SUPPORTED_FORMS:
        raise ValueError(f"Unsupported form: {form}")
    lines, tables = visible_blocks(markup)
    # A 10-Q begins with Part I. Other forms use explicit parts where present.
    part = "I" if form.startswith("10-Q") else ""
    candidates = []
    boundaries = []
    # Record candidate item headings and all places a section must stop.
    # A signature block ends the last item instead of becoming its narrative.
    for index, line in enumerate(lines):
        match = PART.match(line)
        if match and len(line) < 160:
            next_part = match[1].upper()
            # Running page headers repeat the current part, including in the
            # middle of a sentence. Only a change of part ends a section.
            if next_part != part:
                boundaries.append(index)
            part = next_part
            continue
        match = ITEM.match(line)
        if match and len(line) <= 240:
            # The length limit helps reject prose that merely mentions an item.
            item = match[1].upper()
            part = item_part(form, item, part)
            candidates.append((index, part, item, line, ""))
            boundaries.append(index)
        elif re.fullmatch(r"SIGNATURES?", line, re.I):
            boundaries.append(index)
    if not candidates:
        raise ValueError("No item headings found; inspect the document layout/form")

    mdna_key = ("II", "7") if form.startswith("10-K") else ("I", "2")
    original = [c for c in candidates if c[1:3] == mdna_key]
    covered_ranges = [(c[0], next((b for b in boundaries if b > c[0]), len(lines))) for c in original]
    supplemental = descriptive_mdna_span(lines, form, covered_ranges)
    pointers = {}
    if supplemental:
        start, end = supplemental
        # Ordinary item headings already cover their own introductory prose.
        contained = any(c[0] <= start < next((b for b in boundaries if b > c[0]), len(lines))
                        for c in original)
        if not contained:
            title = original[0][3] if original else "Management’s Discussion and Analysis"
            if original:
                old_start = original[0][0]
                old_end = next((b for b in boundaries if b > old_start), len(lines))
                pointers[mdna_key] = lines[old_start:old_end]
            candidates.append((start, *mdna_key, title,
                               "MD&A located by its introductory paragraph and financial-report boundary; "
                               "original item pointer retained when present."))
            boundaries.extend([start, end])
            # In these quarterlies the financial statements also have a
            # descriptive heading. Assign them to Part I Item 1 separately.
            if form.startswith("10-Q") and not any(c[1:3] == ("I", "1") for c in candidates):
                candidates.append((end, "I", "1", "Financial Statements",
                                   "Financial statements identified by their descriptive heading."))
    boundaries = sorted(set(boundaries))

    # Contents entries have short spans. Prefer the occurrence with the most
    # body text before the next item, part or signature boundary.
    best = {}
    for start, part, item, title, note in candidates:
        end = next((b for b in boundaries if b > start), len(lines))
        key = (part, item)
        # Count characters between headings as an estimate of body content.
        score = sum(len(line) for line in lines[start + 1:end])
        # An explicitly bounded descriptive body takes priority over its short
        # item pointer, even when a synthetic test uses only a tiny body.
        if key not in best or (bool(note), score) > (bool(best[key][4]), best[key][0]):
            best[key] = (score, start, end, title, note)

    sections = []
    # Restore document order after choosing one occurrence of each part/item.
    for (part, item), (_, start, end, title, note) in sorted(best.items(), key=lambda x: x[1][1]):
        content = lines[start:end]
        if note and (part, item) in pointers:
            content = pointers[(part, item)] + content
        body = "\n".join(line for line in content if not PART_LABEL.fullmatch(line))
        section_tables = []

        def replace_table(match):
            # Attach each table to this section even when its marker disappears
            # from narrative text. --include-tables restores its text in place.
            value = tables[int(match[1])]
            section_tables.append(value)
            return value if include_tables else ""

        body = TABLE_MARKER.sub(replace_table, body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        sections.append(Section(part, item, title, body, section_tables, note))
    return sections


def filing_inputs(path, form=None):
    """Use manifest metadata to avoid parsing API JSON or the wrong forms."""
    if path.name == "manifest.json":
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            raise ValueError(f"Acquisition manifest is not complete: {path}")
        for filing in manifest["filings"]:
            filing_form = filing["form"]
            if filing_form not in SUPPORTED_FORMS or (form and filing_form != form):
                continue
            name = filing["file"]
            # Acquisition manifests store bare filenames next to the manifest.
            # Reject paths that could point outside that acquisition folder.
            if Path(name).name != name or "\\" in name:
                raise ValueError(f"Invalid manifest filename: {name}")
            yield path.parent / name, filing_form
    elif path.suffix.lower() in {".htm", ".html", ".xml"}:
        # Without a manifest, require the caller to identify the filing form.
        if form is None:
            raise ValueError("Pass --form when parsing an individual filing")
        yield path, form
    else:
        raise ValueError("Input must be a primary HTML/XML filing or manifest.json")


def write_sections(path, form, sections, output):
    """Write narrative and table CSVs with matching source/part/item fields."""
    output.mkdir(parents=True, exist_ok=True)
    section_path = output / f"{path.name}_filing_sections.csv"
    table_path = output / f"{path.name}_filing_tables.csv"
    # The csv module quotes commas/newlines in filing text. newline="" avoids
    # extra blank rows on Windows, and UTF-8 preserves punctuation.
    with section_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Source", "Form", "Part", "Item", "Section", "Text", "TableCount", "ExtractionNote"])
        for section in sections:
            writer.writerow([path.name, form, section.part, section.item,
                             section.title, section.text, len(section.tables), section.note])
    with table_path.open("w", encoding="utf-8", newline="") as handle:
        # Keep removed table text available for auditing the narrative output.
        writer = csv.writer(handle)
        writer.writerow(["Source", "Form", "Part", "Item", "Section", "TableNumber", "Text"])
        for section in sections:
            for number, table in enumerate(section.tables, 1):
                writer.writerow([path.name, form, section.part, section.item,
                                 section.title, number, table])
    return section_path, table_path


def main(argv=None):
    """Parse command-line options and process each selected local filing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Primary filing or acquisition manifest.json")
    parser.add_argument("--form", choices=sorted(SUPPORTED_FORMS),
                        help="Required for a single filing; optional filter for a manifest")
    parser.add_argument("--output", type=Path, default=Path("data/parsed"))
    parser.add_argument("--include-tables", action="store_true",
                        help="Also retain table text inside the section Text column")
    args = parser.parse_args(argv)
    count = 0
    failures = 0
    try:
        for path, form in filing_inputs(args.input, args.form):
            try:
                sections = extract_sections(path.read_bytes(), form, args.include_tables)
                paths = write_sections(path, form, sections, args.output)
                print(f"{path.name}: {len(sections)} sections, "
                      f"{sum(len(s.tables) for s in sections)} tables; {paths[0]}")
                count += 1
            except (OSError, ValueError) as error:
                # Continue with other filings, but report a failing exit code
                # so a partially parsed batch is not mistaken for full success.
                failures += 1
                print(f"Failed {path}: {error}", file=sys.stderr)
        if not count:
            raise ValueError("No filings were successfully parsed")
    except (OSError, ValueError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    # Importing this module exposes its functions without running the CLI.
    raise SystemExit(main())
