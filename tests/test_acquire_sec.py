"""Offline contract tests; no requests to SEC are made."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location(
    "acquire_sec", Path(__file__).resolve().parents[1] / "scripts/acquire_sec.py")
sec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sec)

CIK = "0000320193"
BASE = f"https://data.sec.gov/submissions/CIK{CIK}.json"
UA = "BDA research contact@example.com"


def row(number=1, form="10-K", date="2025-11-01", document="annual.htm"):
    return dict(form=form, accessionNumber=f"0000320193-25-{number:06d}",
                filingDate=date, reportDate="2025-09-30", primaryDocument=document, items="")


def columns(rows):
    return {key: [r[key] for r in rows] for key in row()}


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, bytes) else json.dumps(value).encode()


class AcquisitionTests(unittest.TestCase):
    def args(self, output, *extra):
        return sec.arguments(["--cik", CIK, "--user-agent", UA, "--output", str(output),
                              "--forms", "10-K", *extra])

    def submissions(self, rows, files=None):
        return {"cik": int(CIK), "name": "APPLE INC", "filings": {
            "recent": columns(rows), "files": files or []}}

    def test_selection_exact_forms_dates_limits_and_deduplication(self):
        rows = [row(1), row(2, date="2024-11-01"), row(3, form="10-K/A")]
        selected = sec.select_filings(rows, ["10-K", "10-K"], 1, [], "2024-12-31", "2024-01-01")
        self.assertEqual([r["accessionNumber"] for r in selected], [rows[1]["accessionNumber"]])
        self.assertEqual(sec.select_filings(rows, [], 1, [rows[2]["accessionNumber"]] * 2, None), [rows[2]])
        with self.assertRaisesRegex(ValueError, "not found"):
            sec.select_filings(rows, [], 1, ["missing"], None)

    def test_historical_arrays_can_omit_optional_columns(self):
        data = columns([row()])
        del data["items"]
        del data["reportDate"]
        self.assertEqual(sec.filing_rows(data)[0]["items"], "")
        data["form"].append("8-K")
        with self.assertRaises(ValueError):
            sec.filing_rows(data)

    def test_invalid_arguments_rejected_before_network(self):
        invalid = [["--min-interval", "nan"], ["--min-interval", "0.01"],
                   ["--min-interval", "inf"], ["--limit-per-form", "0"],
                   ["--user-agent", "anonymous"], ["--user-agent", "BDA\ncontact@example.com"],
                   ["--from-date", "2025-02-30"], ["--as-of", "2025-1-01"],
                   ["--from-date", "2025-01-01", "--as-of", "2024-01-01"],
                   ["--accession", "bad"], ["--forms", "../../10-K"],
                   ["--accession", row()["accessionNumber"], "--as-of", "2025-01-01"]]
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                sec.validate(self.args("unused", *extra))
        for cik in ("0", "1/2", "12345678901"):
            with self.subTest(cik=cik), self.assertRaises(ValueError):
                args = self.args("unused")
                args.cik = cik
                sec.validate(args)

    def test_document_path_and_accession_are_validated(self):
        for name in ("../bad.htm", "https://evil.test/a.htm", "a.htm?x=1", "/bad.htm"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                sec.archive_url(CIK, row(document=name))
        for name in ("annual.htm", "annual.html", "filing.txt", "primary.xml"):
            self.assertTrue(sec.archive_url(CIK, row(document=name)).endswith(name))
        with self.assertRaises(ValueError):
            sec.archive_url(CIK, {**row(), "accessionNumber": "../bad"})

    def test_access_block_response_is_rejected(self):
        for body in (b"", b"<html>SEC.gov | Your request</html>",
                     b"<html>Undeclared Automated Tool</html>", b'{"error": "bad"}'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                sec.validate_document(body, "annual.htm")

    def test_complete_run_and_separate_repeat_run(self):
        filing = row()
        body = b"<!doctype html><html>Annual filing</html>"
        client = FakeClient({BASE: self.submissions([filing]),
                             sec.archive_url(CIK, filing): body})
        with tempfile.TemporaryDirectory() as output, contextlib.redirect_stdout(io.StringIO()):
            first = sec.acquire(self.args(output), client)
            second = sec.acquire(self.args(output), client)
            manifest = json.loads(first.read_text())
            self.assertNotEqual(first, second)
            self.assertEqual(manifest["status"], "complete")
            record = manifest["filings"][0]
            self.assertEqual(record["sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual((first.parent / record["file"]).read_bytes(), body)
            self.assertEqual(len(manifest["api_files"]), 1)

    def test_history_pagination_date_pruning_and_overlap(self):
        recent = row()
        old = row(2, date="2020-11-01")
        name = f"CIK{CIK}-submissions-001.json"
        blocks = [{"name": name, "filingFrom": "2019-01-01", "filingTo": "2021-12-31"},
                  {"name": "should-not-fetch.json", "filingFrom": "2000-01-01", "filingTo": "2001-01-01"}]
        client = FakeClient({BASE: self.submissions([recent], blocks),
                             f"https://data.sec.gov/submissions/{name}": columns([old, recent]),
                             sec.archive_url(CIK, old): b"<html>Older report</html>"})
        with tempfile.TemporaryDirectory() as output, contextlib.redirect_stdout(io.StringIO()):
            path = sec.acquire(self.args(output, "--include-history", "--from-date", "2020-01-01", "--as-of", "2020-12-31"), client)
            manifest = json.loads(path.read_text())
            self.assertEqual(manifest["filings"][0]["accessionNumber"], old["accessionNumber"])
            self.assertEqual(len(manifest["api_files"]), 2)

    def test_interrupted_run_preserves_completed_files_and_failure(self):
        first, second = row(), row(2, form="10-Q", document="quarter.htm")
        client = FakeClient({BASE: self.submissions([first, second]),
                             sec.archive_url(CIK, first): b"<html>Annual report</html>",
                             sec.archive_url(CIK, second): RuntimeError("temporary failure")})
        with tempfile.TemporaryDirectory() as output, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "temporary failure"):
                sec.acquire(self.args(output, "--forms", "10-K", "10-Q"), client)
            path = next(Path(output).rglob("manifest.json"))
            manifest = json.loads(path.read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(len(manifest["filings"]), 1)
            self.assertEqual(len(manifest["selected_accessions"]), 2)
            self.assertTrue((path.parent / manifest["filings"][0]["file"]).is_file())

    def test_no_matches_and_mismatched_company_leave_failed_manifest(self):
        for data in (self.submissions([]), {**self.submissions([row()]), "cik": 1}):
            with tempfile.TemporaryDirectory() as output, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    sec.acquire(self.args(output), FakeClient({BASE: data}))
                manifest = json.loads(next(Path(output).rglob("manifest.json")).read_text())
                self.assertEqual(manifest["status"], "failed")

    def test_ticker_lookup_is_exact_and_case_insensitive(self):
        args = sec.arguments(["--ticker", "aapl", "--user-agent", UA])
        client = FakeClient({"https://www.sec.gov/files/company_tickers.json": {
            "0": {"ticker": "AAPL", "cik_str": int(CIK)}}})
        self.assertEqual(sec.resolve_cik(args, client), CIK)
        args.ticker = "AAP"
        with self.assertRaisesRegex(ValueError, "use --cik"):
            sec.resolve_cik(args, client)

    def test_optional_companyfacts_and_shortfall_warning(self):
        filing = row()
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
        client = FakeClient({BASE: self.submissions([filing]),
                             sec.archive_url(CIK, filing): b"<html>Annual report</html>",
                             facts_url: {"cik": int(CIK), "facts": {}}})
        with tempfile.TemporaryDirectory() as output, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            manifest = json.loads(sec.acquire(self.args(output, "--companyfacts", "--limit-per-form", "2"), client).read_text())
            self.assertEqual(len(manifest["api_files"]), 2)
            self.assertTrue(manifest["warnings"])

    @patch.object(sec.time, "sleep")
    @patch.object(sec, "urlopen")
    def test_retry_and_request_identity(self, urlopen, sleep):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        urlopen.side_effect = [HTTPError(BASE, 429, "slow down", {"Retry-After": "3"}, None), response]
        self.assertEqual(sec.SecClient(UA, 0.2).get(BASE), b"ok")
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn(unittest.mock.call(3.0), sleep.call_args_list)
        self.assertEqual(urlopen.call_args.args[0].get_header("User-agent"), UA)

    @patch.object(sec.time, "sleep")
    @patch.object(sec, "urlopen")
    def test_block_is_not_retried_and_server_retries_are_bounded(self, urlopen, sleep):
        urlopen.side_effect = HTTPError(BASE, 403, "blocked", {}, None)
        with self.assertRaisesRegex(RuntimeError, "403"):
            sec.SecClient(UA, 0.2).get(BASE)
        self.assertEqual(urlopen.call_count, 1)
        urlopen.reset_mock()
        urlopen.side_effect = HTTPError(BASE, 503, "unavailable", {}, None)
        with self.assertRaises(HTTPError):
            sec.SecClient(UA, 0.2).get(BASE)
        self.assertEqual(urlopen.call_count, 4)


if __name__ == "__main__":
    unittest.main()
