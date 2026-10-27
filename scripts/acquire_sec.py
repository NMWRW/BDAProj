#!/usr/bin/env python3
"""Acquire a company's SEC EDGAR primary filings and a provenance manifest.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FORMS = ("10-K", "10-Q", "8-K")
RETRYABLE = {429, 500, 502, 503, 504}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    company = parser.add_mutually_exclusive_group(required=True)
    company.add_argument("--cik", help="Company CIK, with or without leading zeros")
    company.add_argument("--ticker", help="Exact SEC-listed ticker, e.g. AAPL")
    parser.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT"),
                        help="Application name and contact email; defaults to SEC_USER_AGENT")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/sec")
    parser.add_argument("--forms", nargs="+", default=list(FORMS),
                        help="Exact form names; amendments must be requested explicitly")
    parser.add_argument("--limit-per-form", type=int, default=1)
    parser.add_argument("--accession", action="append", default=[],
                        help="Exact accession; repeat to select multiple filings")
    parser.add_argument("--from-date", help="Inclusive filing-date lower bound, YYYY-MM-DD")
    parser.add_argument("--as-of", help="Inclusive filing-date upper bound, YYYY-MM-DD")
    parser.add_argument("--include-history", action="store_true",
                        help="Also fetch older submissions blocks within the date range")
    parser.add_argument("--companyfacts", action="store_true",
                        help="Also save XBRL companyfacts; not required for filing text")
    parser.add_argument("--min-interval", type=float, default=0.2,
                        help="Seconds between requests; default 0.2 (5/sec per process)")
    return parser.parse_args(argv)


def validate(args):
    if args.cik and (not re.fullmatch(r"[0-9]{1,10}", args.cik) or int(args.cik) == 0):
        raise ValueError("CIK must contain 1–10 digits and be greater than zero")
    if args.ticker and not re.fullmatch(r"[A-Za-z0-9.-]{1,20}", args.ticker):
        raise ValueError("Invalid ticker")
    if (args.limit_per_form < 1 or not math.isfinite(args.min_interval)
            or args.min_interval < 0.1):
        raise ValueError("Use --limit-per-form >= 1 and a finite --min-interval >= 0.1")
    if (not args.user_agent or any(c in args.user_agent for c in "\r\n")
            or not re.fullmatch(r"[^\r\n]+\s+[^\s@]+@[^\s@]+\.[^\s@]+",
                                args.user_agent.strip())):
        raise ValueError("Set SEC_USER_AGENT or --user-agent to an application name and contact email")
    for value in (args.from_date, args.as_of):
        if value:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("Dates must use YYYY-MM-DD")
            datetime.strptime(value, "%Y-%m-%d")
    if args.from_date and args.as_of and args.from_date > args.as_of:
        raise ValueError("--from-date must be on or before --as-of")
    if args.accession and (args.from_date or args.as_of):
        raise ValueError("Choose explicit accessions or a filing-date range, not both")
    for form in args.forms:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 /-]{0,30}", form):
            raise ValueError(f"Invalid form: {form!r}")
    for accession in args.accession:
        if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
            raise ValueError(f"Invalid accession: {accession}")


class SecClient:
    def __init__(self, user_agent, min_interval):
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.last_request = 0.0

    def get(self, url):
        for attempt in range(4):
            delay = self.min_interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            request = Request(url, headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json,text/html,*/*",
                "Accept-Encoding": "identity",
            })
            self.last_request = time.monotonic()
            try:
                with urlopen(request, timeout=30) as response:
                    return response.read()
            except HTTPError as error:
                error.close()
                if error.code == 403:
                    raise RuntimeError(
                        "SEC returned 403. Check the User-Agent contact, slow requests, "
                        "and retry later; do not use alternate identities to evade a block."
                    ) from error
                if error.code not in RETRYABLE or attempt == 3:
                    raise
                wait = 2 ** attempt
                if error.headers.get("Retry-After"):
                    value = error.headers["Retry-After"]
                    try:
                        wait = max(wait, min(float(value), 60))
                    except ValueError:
                        try:
                            wait = max(wait, min((parsedate_to_datetime(value) -
                                                  datetime.now(timezone.utc)).total_seconds(), 60))
                        except (TypeError, ValueError):
                            pass
            except (URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
                wait = 2 ** attempt
            time.sleep(wait)
        raise AssertionError("unreachable")


def filing_rows(columns):
    """Decode both recent and historical SEC parallel arrays."""
    required = ("form", "accessionNumber", "filingDate", "primaryDocument")
    optional = ("reportDate", "items")
    length = len(columns["form"])
    if any(len(columns.get(key, [])) != length for key in required):
        raise ValueError("Unexpected SEC submissions column lengths")
    if any(key in columns and len(columns[key]) != length for key in optional):
        raise ValueError("Unexpected SEC optional column lengths")
    return [{key: columns[key][i] if key in columns else ""
             for key in required + optional} for i in range(length)]


def select_filings(rows, forms, limit_per_form, accessions, as_of, from_date=None):
    if accessions:
        found = {row["accessionNumber"]: row for row in rows}
        missing = set(accessions) - found.keys()
        if missing:
            raise ValueError(f"Accession(s) not found: {', '.join(sorted(missing))}; "
                             "try --include-history for older filings")
        return [found[a] for a in dict.fromkeys(accessions)]
    selected = []
    for form in dict.fromkeys(forms):
        matches = [r for r in rows if r["form"] == form and
                   (as_of is None or r["filingDate"] <= as_of) and
                   (from_date is None or r["filingDate"] >= from_date)]
        selected.extend(sorted(matches, key=lambda r: (r["filingDate"],
                            r["accessionNumber"]), reverse=True)[:limit_per_form])
    return selected


def archive_url(cik, row):
    accession = row["accessionNumber"]
    document = row["primaryDocument"]
    if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        raise ValueError(f"Invalid SEC accession: {accession!r}")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*\.(?:html?|txt|xml)", document, re.I):
        raise ValueError(f"Unsupported primary document name: {document!r}")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{document}")


def validate_document(body, document):
    head = body[:10000].lower()
    if (not body.strip() or b"sec.gov | your request" in head
            or b"undeclared automated tool" in head
            or b"request rate threshold exceeded" in head):
        raise ValueError("SEC returned an empty document or access-block page")
    if document.lower().endswith((".htm", ".html")) and b"<html" not in head:
        raise ValueError("SEC response was not a filing HTML document")


def resolve_cik(args, client):
    if args.cik:
        return args.cik.zfill(10)
    companies = json.loads(client.get("https://www.sec.gov/files/company_tickers.json"))
    matches = {str(c["cik_str"]).zfill(10) for c in companies.values()
               if c["ticker"].upper() == args.ticker.upper()}
    if len(matches) != 1:
        raise ValueError(f"Ticker {args.ticker!r} not uniquely found; use --cik")
    return matches.pop()


def write_file(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(body)
    temporary.replace(path)
    return {"file": path.name, "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


def acquire(args, client=None):
    """Return the completed manifest path; leave a failed manifest on interruption."""
    validate(args)
    client = client or SecClient(args.user_agent, args.min_interval)
    cik = resolve_cik(args, client)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output = args.output.expanduser().resolve() / f"CIK{cik}" / run_id
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = output / "manifest.json"
    manifest = {
        "schema_version": 1, "status": "in_progress", "cik": cik,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": {"forms": args.forms, "limit_per_form": args.limit_per_form,
                      "accessions": args.accession, "from_date": args.from_date,
                      "as_of": args.as_of, "include_history": args.include_history},
        "filings": [], "api_files": [], "warnings": [],
    }

    def save_manifest():
        write_file(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())

    def save_api(url, name):
        body = client.get(url)
        data = json.loads(body)
        metadata = {"source_url": url, **write_file(output / name, body)}
        manifest["api_files"].append(metadata)
        save_manifest()
        return data

    save_manifest()
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        submissions = save_api(url, f"submissions_CIK{cik}.json")
        if str(submissions["cik"]).zfill(10) != cik:
            raise ValueError("Submissions CIK did not match request")
        manifest["company"] = submissions["name"]
        rows = filing_rows(submissions["filings"]["recent"])
        if args.include_history:
            for block in submissions["filings"].get("files", []):
                if args.from_date and block.get("filingTo", "9999") < args.from_date:
                    continue
                if args.as_of and block.get("filingFrom", "0000") > args.as_of:
                    continue
                name = block["name"]
                if not re.fullmatch(r"CIK" + cik + r"-submissions-[0-9]+\.json", name):
                    raise ValueError(f"Unexpected history filename: {name!r}")
                history = save_api(f"https://data.sec.gov/submissions/{name}", name)
                rows.extend(filing_rows(history))
        rows = list({row["accessionNumber"]: row for row in reversed(rows)}.values())
        selected = select_filings(rows, args.forms, args.limit_per_form,
                                  args.accession, args.as_of, args.from_date)
        if not selected:
            raise ValueError("No matching filings; check selection or try --include-history")
        manifest["selected_accessions"] = [r["accessionNumber"] for r in selected]
        if not args.accession:
            for form in dict.fromkeys(args.forms):
                count = sum(r["form"] == form for r in selected)
                if count < args.limit_per_form:
                    manifest["warnings"].append(
                        f"Requested {args.limit_per_form} {form}; found {count} in searched history")
        save_manifest()
        for row in selected:
            url = archive_url(cik, row)
            body = client.get(url)
            validate_document(body, row["primaryDocument"])
            name = f"{row['accessionNumber']}_{row['primaryDocument']}"
            manifest["filings"].append({**row, "source_url": url,
                                        **write_file(output / name, body)})
            save_manifest()
            print(f"Saved {name} ({len(body):,} bytes)")
        if args.companyfacts:
            facts = save_api(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                             f"companyfacts_CIK{cik}.json")
            if str(facts["cik"]).zfill(10) != cik:
                raise ValueError("Companyfacts CIK did not match request")
        manifest["status"] = "complete"
    except (Exception, KeyboardInterrupt) as error:
        manifest["status"] = "failed"
        manifest["error"] = str(error) or type(error).__name__
        raise
    finally:
        manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_manifest()
        print(f"Manifest: {manifest_path}")
    for warning in manifest["warnings"]:
        print(f"Warning: {warning}", file=sys.stderr)
    return manifest_path


def main():
    try:
        acquire(arguments())
    except (HTTPError, URLError, ValueError, RuntimeError, KeyError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
