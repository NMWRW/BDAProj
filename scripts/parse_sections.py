"""Extract SEC filing sections with tabular data separate from narrative.

Accept a primary HTML filing or a completed acquire_sec.py manifest.
Original downloaded documents are never modified. No network access is used.

Run from the repository root: python3 scripts/parse_sections.py --help

Processing order:
1. Retain heading/anchor evidence while preparing the filing HTML.
2. Select acceptable part/item starts in source-document order.
3. Extract between selected starts and separate financial-table text.
4. Write narrative/table CSVs and a diagnostic section-index JSON file.
"""

import argparse
from collections import Counter, defaultdict
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
# Vocabulary, not ordering constraints. Items can appear in any document order.
# Sources: https://www.sec.gov/files/form10-k.pdf, form10-q.pdf, form8-k.pdf
# The catalogue includes optional/current items; their presence is not required.
ANNUAL_ITEMS = frozenset([str(n) for n in range(1, 17)] +
                         ["1A", "1B", "1C", "7A", "9A", "9B", "9C"])
QUARTERLY_ITEMS = {"I": frozenset({"1", "2", "3", "4"}),
                   "II": frozenset({"1", "1A", "2", "3", "4", "5", "6"})}
CURRENT_ITEMS = frozenset(
    f"{section}.{item:02}" for section, count in
    ((1, 5), (2, 6), (3, 3), (4, 2), (5, 8), (6, 6), (7, 1), (8, 1), (9, 1))
    for item in range(1, count + 1))
# A trailing heading period is allowed; decimal item numbers remain one token.
ITEM_NUMBER = re.compile(r"\d{1,2}(?:\.\d{2})?(?:\s*[A-C])?(?:\(T\))?(?!\w|\.\d)", re.I)
REFERENCE = re.compile(r"^(?:of|in|under|above|below|contains|discusses|describes|provides|see|refer)\b", re.I)
CONTINUED = re.compile(r"\bcontinued\b|\bcont\.(?:\s|\)|$)", re.I)


def parse_heading(text):
    """Read a single item or an explicit grouped list without guessing ranges."""
    prefix = re.match(r"^Items?\s+", text, re.I)
    if prefix is None:
        return None
    rest = text[prefix.end():]
    match = ITEM_NUMBER.match(rest)
    if match is None:
        return None
    items = [re.sub(r"\s+|\(T\)", "", match[0], flags=re.I).upper()]
    rest = rest[match.end():]
    while True:
        separator = re.match(r"^(?:\s*,\s*(?:(?:and|&)\s*)?|\s+(?:and|&)\s+)", rest, re.I)
        match = ITEM_NUMBER.match(rest[separator.end():]) if separator else None
        if match is None:
            break
        items.append(re.sub(r"\s+|\(T\)", "", match[0], flags=re.I).upper())
        rest = rest[separator.end() + match.end():]
    range_prefix = re.match(r"^\s*(?:[-–—]|through|to)\s*", rest, re.I)
    endpoint = ITEM_NUMBER.match(rest[range_prefix.end():]) if range_prefix else None
    if endpoint:
        tail = rest[range_prefix.end() + endpoint.end():].lstrip(". :–—-\t")
        if REFERENCE.match(tail):
            return None
        raise ValueError("Ranged item heading needs review; use explicit group membership")
    return tuple(dict.fromkeys(items)), rest.lstrip(". :–—-\t")


@dataclass(frozen=True)
class TextBlock:
    """One prepared text block, with heading evidence retained from the DOM."""
    position: int
    text: str
    anchors: tuple = ()
    links: tuple = ()
    emphasized: bool = False


@dataclass(frozen=True)
class HeadingCandidate:
    start: int
    part: str
    items: tuple
    title: str
    evidence: tuple = ()
    note: str = ""
    end_limit: int | None = None
    heading_end: int = 0

    @property
    def keys(self):
        return tuple((self.part, item) for item in self.items)


@dataclass
class SectionIndex:
    """Selected starts are separate from candidates; only starts bound items."""
    form: str
    blocks: list
    tables: dict
    anchor_positions: dict
    starts: list
    stops: tuple
    rejected: list
    pointers: dict

    def end_for(self, heading):
        next_start = next((c.start for c in self.starts if c.start > heading.start), len(self.blocks))
        stop = next((p for p in self.stops if p > heading.start), len(self.blocks))
        return min(next_start, stop, heading.end_limit if heading.end_limit is not None else len(self.blocks))


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
    items: tuple = ()
    spans: tuple = ()
    evidence: tuple = ()


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


def read_document(markup):
    """Prepare blocks while retaining anchors/formatting for boundary selection.

    This is not a lossless raw-HTML ledger: hidden markup, contents and page
    furniture are still cleaned. Block positions refer to this prepared stream.
    """
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

    # Read contents links BEFORE removing contents tables. A heading's anchor
    # evidence must not disappear during cleanup. Split-cell rows are combined.
    contents_links = []
    for anchor in soup.find_all("a", href=True):
        if not anchor["href"].startswith("#"):
            continue
        label = anchor.find_parent("tr") or anchor
        try:
            parsed = parse_heading(normalize(label.get_text(" ", strip=True)))
        except ValueError:
            parsed = None
        if parsed:
            contents_links.append((anchor["href"][1:], parsed[0]))

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

    raw_blocks = []
    pieces, anchors, links = [], set(), set()
    emphasized = False

    def flush():
        nonlocal emphasized
        text = normalize("".join(pieces))
        pieces.clear()
        if text:
            raw_blocks.append(TextBlock(len(raw_blocks), text, tuple(sorted(anchors)),
                                        tuple(sorted(links)), emphasized))
            anchors.clear()
            links.clear()
            emphasized = False
        # Empty <a id=...> and <div id=...> anchors belong to the next block.

    def walk(node, strong=False, href=None):
        """Walk HTML once, keeping source order and evidence with each block."""
        nonlocal emphasized
        if isinstance(node, (Comment, Declaration, Doctype, ProcessingInstruction)):
            return
        if isinstance(node, NavigableString):
            for i, fragment in enumerate(str(node).split("\n")):
                if i:
                    flush()
                pieces.append(fragment)
                if fragment.strip():
                    emphasized |= strong
                    if href:
                        links.add(href)
        elif isinstance(node, Tag):
            block = node.name in BLOCKS
            if block or node.name in {"br", "hr"}:
                flush()
            for attr in ("id", "name"):
                if node.get(attr):
                    anchors.add(str(node[attr]))
            style = str(node.get("style", ""))
            strong = strong or node.name in {"h1", "h2", "h3", "h4", "h5", "h6", "b", "strong"} or bool(
                re.search(r"font-weight\s*:\s*(?:bold|[6-9]00)", style, re.I))
            if node.name == "a" and node.get("href"):
                href = node["href"]
            for child in node.children:
                walk(child, strong, href)
            if block:
                flush()
            elif node.name in {"td", "th"}:
                pieces.append(" ")

    walk(soup.body or soup)
    flush()
    occurrences = Counter(b.text for b in raw_blocks)
    blocks = []
    for block in raw_blocks:
        line = block.text
        if (re.fullmatch(r"\d+", line) or line.casefold() == "table of contents"
                or re.search(r"(?:\||/).*Form\s+10-[KQ](?:\s*\|\s*\d+)?$", line, re.I)
                or (occurrences[line] >= 3 and len(line) < 90
                    and re.fullmatch(r".+(?:Inc\.|Corp\.|& Co\.)", line))):
            continue
        blocks.append(TextBlock(len(blocks), line, block.anchors, block.links, block.emphasized))
    anchor_positions = defaultdict(list)
    for block in blocks:
        for anchor in block.anchors:
            anchor_positions[anchor].append(block.position)
    return blocks, tables, {key: tuple(value) for key, value in anchor_positions.items()}, contents_links


def visible_blocks(markup):
    """Compatibility helper returning prepared text and separated tables."""
    blocks, tables, _, _ = read_document(markup)
    return [b.text for b in blocks], tables


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


def _allowed(form, part, item):
    if form.startswith("10-K"):
        return item in ANNUAL_ITEMS
    if form.startswith("10-Q"):
        return item in QUARTERLY_ITEMS.get(part, ())
    return item in CURRENT_ITEMS


def _title_line(text):
    return len(text) < 180 and bool(re.match(
        r"^(?:Business|Financial Statements|Management[’']s Discussion|Risk Factors|"
        r"Unresolved Staff Comments|Staff Comments|Properties|Legal Proceedings|"
        r"Other Information|Controls|Exhibits|Quantitative|Mine Safety|Defaults|"
        r"Unregistered|Reserved|Cybersecurity|Market Risk)\b", text, re.I))


def build_section_index(markup, form="10-Q"):
    """Confirm a section map first; numeric item order is never a constraint.

    Dictionaries retain competing candidates for each part/item. The selected
    starts, kept in source order, are the only item-to-item stopping points.
    """
    if form not in SUPPORTED_FORMS:
        raise ValueError(f"Unsupported form: {form}")
    blocks, tables, anchor_positions, contents_links = read_document(markup)
    lines = [block.text for block in blocks]
    part = "I" if form.startswith("10-Q") else ""
    candidates, rejected, stops = [], [], []
    for position, block in enumerate(blocks):
        line = block.text
        match = PART.match(line)
        part_heading = PART_LABEL.fullmatch(line) or re.fullmatch(
            r"PART\s+(?:IV|III|II|I)[.\s:–—-]*(?:FINANCIAL INFORMATION|OTHER INFORMATION)", line, re.I)
        if match and part_heading:
            next_part = match[1].upper()
            if next_part != part:
                stops.append(position)
            part = next_part
            continue
        if re.fullmatch(r"SIGNATURES?", line, re.I):
            stops.append(position)
            continue
        parsed = parse_heading(line)
        if parsed is None:
            continue
        items, title = parsed
        if len(line) > 240 or REFERENCE.match(title):
            rejected.append({"position": position, "reason": "reference_or_long_prose", "text": line})
            continue
        item_parts = {item_part(form, item, part) for item in items}
        if not all(_allowed(form, item_part(form, item, part), item) for item in items):
            rejected.append({"position": position, "reason": "not_in_form_vocabulary", "text": line})
            continue
        if len(item_parts) != 1:
            raise ValueError(f"Grouped heading crosses parts and needs review: {line}")
        candidate_part = next(iter(item_parts))
        part = candidate_part
        evidence = ["form_vocabulary", "block_start"]
        if block.emphasized:
            evidence.append("styled_heading")
        if CONTINUED.search(title):
            evidence.append("continued_header")
        heading_end = position + 1
        full_title = line
        if not title and heading_end < len(lines) and _title_line(lines[heading_end]):
            full_title += " " + lines[heading_end]
            heading_end += 1
            evidence.append("split_heading")
        candidates.append(HeadingCandidate(position, part, items, full_title,
                                           tuple(evidence), heading_end=heading_end))

    # Resolve link targets by their actual document positions. Empty anchors,
    # nested wrappers and PART labels may precede the heading by a few blocks.
    anchored = set()
    by_position = {c.start: c for c in candidates}
    for anchor, members in contents_links:
        for position in anchor_positions.get(anchor, ()):
            for probe in range(position, min(position + 4, len(blocks))):
                candidate = by_position.get(probe)
                if candidate:
                    if set(members).issubset(candidate.items):
                        anchored.add(probe)
                    break
                if probe > position and not PART.match(lines[probe - 1]):
                    break
    candidates = [HeadingCandidate(c.start, c.part, c.items, c.title,
                                   c.evidence + (("contents_anchor",) if c.start in anchored else ()),
                                   c.note, c.end_limit, c.heading_end) for c in candidates]

    # Provisional spans are only evidence for selecting duplicates. They NEVER
    # become final section endings. Prefer linked starts, starts with body text,
    # then heading formatting; otherwise keep the first substantive occurrence
    # so repeated page headings cannot discard the opening pages.
    provisional = sorted(set([c.start for c in candidates] + stops + [len(blocks)]))

    def priority(candidate):
        end = next(p for p in provisional if p > candidate.start)
        has_body = any(not PART_LABEL.fullmatch(line) and not re.fullmatch(r"\d+(?:\s*[-–—]\s*\d+)?", line)
                       for line in lines[candidate.heading_end:end])
        return ("contents_anchor" in candidate.evidence, has_body,
                "styled_heading" in candidate.evidence, -candidate.start)

    alternatives = defaultdict(list)
    for candidate in candidates:
        alternatives[candidate.keys].append(candidate)
    starts = []
    for choices in alternatives.values():
        regular = [c for c in choices if "continued_header" not in c.evidence]
        chosen = max(regular or choices, key=priority)
        starts.append(chosen)
        for candidate in choices:
            if candidate is not chosen:
                rejected.append({"position": candidate.start, "end": candidate.heading_end,
                                 "reason": "duplicate_heading", "text": candidate.title})
    # A combined heading and a separate heading for one of its members must
    # not silently compete for ownership of the same item identity.
    owner = {}
    for candidate in starts:
        for key in candidate.keys:
            if key in owner:
                raise ValueError(f"Overlapping grouped item identities need review: {key}")
            owner[key] = candidate
    index = SectionIndex(form, blocks, tables, anchor_positions, sorted(starts, key=lambda c: c.start),
                         tuple(sorted(set(stops))), rejected, {})

    # Preserve the existing, tested same-document MD&A recovery. Its end is
    # explicit, and its original pointer remains a separate recorded span.
    mdna_key = ("II", "7") if form.startswith("10-K") else ("I", "2")
    original = next((c for c in index.starts if mdna_key in c.keys), None)
    covered = [(original.start, index.end_for(original))] if original else []
    supplemental = descriptive_mdna_span(lines, form, covered)
    if supplemental:
        start, end = supplemental
        if original and len(original.items) != 1:
            raise ValueError("Referenced MD&A in a grouped item needs review")
        if original:
            index.pointers[mdna_key] = (original.start, index.end_for(original))
            # The confirmed physical pointer still ends the preceding item;
            # moving its substantive body must not duplicate it into that item.
            index.stops = tuple(sorted(set(index.stops + (original.start,))))
            index.starts.remove(original)
        index.starts.append(HeadingCandidate(start, mdna_key[0], (mdna_key[1],),
                            original.title if original else "Management’s Discussion and Analysis",
                            ("descriptive_mdna",),
                            "MD&A recovered from its introduction to a financial-report boundary; "
                            "original item pointer retained when present.", end, start))
        index.stops = tuple(sorted(set(index.stops + (end,))))
        if form.startswith("10-Q") and not any(("I", "1") in c.keys for c in index.starts):
            index.starts.append(HeadingCandidate(end, "I", ("1",), "Financial Statements",
                                ("descriptive_financial_statements",),
                                "Financial statements identified by their descriptive heading.", heading_end=end + 1))
        index.starts.sort(key=lambda c: c.start)
    if not index.starts:
        raise ValueError("No item headings found; inspect the document layout/form")
    return index


def extract_sections(markup, form="10-Q", include_tables=False, *, section_index=None):
    """Slice between accepted starts, preserving document order and group spans."""
    index = section_index or build_section_index(markup, form)
    if index.form != form:
        raise ValueError("Section index form does not match requested form")
    suppressed = {p for r in index.rejected if r["reason"] == "duplicate_heading"
                  for p in range(r["position"], r["end"])}
    sections = []
    for heading in index.starts:
        end = index.end_for(heading)
        spans = ((heading.start, end),)
        pointer = index.pointers.get((heading.part, heading.items[0]))
        if pointer:
            spans = (pointer,) + spans
        content = [block.text for start, stop in spans for block in index.blocks[start:stop]
                   if block.position not in suppressed and not PART_LABEL.fullmatch(block.text)]
        body = "\n".join(content)
        section_tables = []

        def replace_table(match):
            # Attach each table to this section even when its marker disappears
            # from narrative text. --include-tables restores its text in place.
            value = index.tables[int(match[1])]
            section_tables.append(value)
            return value if include_tables else ""

        body = TABLE_MARKER.sub(replace_table, body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        sections.append(Section(heading.part, ";".join(heading.items), heading.title, body,
                                section_tables, heading.note, heading.items, spans, heading.evidence))
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


def write_sections(path, form, sections, output, *, section_index=None):
    """Write narrative and table CSVs with matching source/part/item fields."""
    output.mkdir(parents=True, exist_ok=True)
    section_path = output / f"{path.name}_filing_sections.csv"
    table_path = output / f"{path.name}_filing_tables.csv"
    # The csv module quotes commas/newlines in filing text. newline="" avoids
    # extra blank rows on Windows, and UTF-8 preserves punctuation.
    with section_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Source", "Form", "Part", "Item", "Section", "Text", "TableCount", "ExtractionNote",
                         "ItemMembers", "BlockSpans", "BoundaryEvidence"])
        for section in sections:
            writer.writerow([path.name, form, section.part, section.item,
                             section.title, section.text, len(section.tables), section.note,
                             json.dumps(section.items or (section.item,)), json.dumps(section.spans),
                             json.dumps(section.evidence)])
    with table_path.open("w", encoding="utf-8", newline="") as handle:
        # Keep removed table text available for auditing the narrative output.
        writer = csv.writer(handle)
        writer.writerow(["Source", "Form", "Part", "Item", "Section", "TableNumber", "Text", "ItemMembers"])
        for section in sections:
            for number, table in enumerate(section.tables, 1):
                writer.writerow([path.name, form, section.part, section.item,
                                 section.title, number, table, json.dumps(section.items or (section.item,))])
    if section_index is not None:
        # This diagnostic describes decisions in the prepared text stream; it
        # does not claim every raw HTML node has been inventoried or validated.
        evidence = {"source": path.name, "form": form, "parser_version": 2,
                    "position_basis": "prepared_text_blocks", "block_count": len(section_index.blocks),
                    "anchor_positions": section_index.anchor_positions,
                    "explicit_stops": [{"position": p, "text": section_index.blocks[p].text}
                                       for p in section_index.stops],
                    "sections": [{"part": s.part, "items": s.items, "title": s.title,
                                  "spans": s.spans, "evidence": s.evidence, "note": s.note}
                                 for s in sections], "rejected_candidates": section_index.rejected}
        (output / f"{path.name}_section_index.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
                markup = path.read_bytes()
                index = build_section_index(markup, form)
                sections = extract_sections(markup, form, args.include_tables, section_index=index)
                paths = write_sections(path, form, sections, args.output, section_index=index)
                print(f"{path.name}: {len(sections)} sections, "
                      f"{sum(len(s.tables) for s in sections)} tables; {paths[0]}")
                count += 1
                unknown = sum(r["reason"] == "not_in_form_vocabulary" for r in index.rejected)
                if unknown:
                    print(f"Review {path.name}: {unknown} heading-like blocks outside the form vocabulary; "
                          "see its section_index.json", file=sys.stderr)
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
