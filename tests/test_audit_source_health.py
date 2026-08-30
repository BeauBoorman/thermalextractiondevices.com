"""Source-health auditor tests: redirects, backoff, decay confirmation.

Uses an in-process HTTP server (the repo's test_fetch.py pattern) so no
network is required. Timing-sensitive paths get short windows.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from scripts.audit_source_health import (
    classify_cause,
    load_sources,
    main,
    parse_iso,
)


class _Handler(BaseHTTPRequestHandler):
    """Serves /ok, /redirect, /chain, /gone (410), /missing (404),
    /rate (429 with Retry-After), /forbidden (403), /nohead (405 on HEAD),
    /timeout (never answers)."""

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(head=True)

    def do_GET(self) -> None:  # noqa: N802
        self._serve(head=False)

    def _serve(self, head: bool) -> None:
        path = self.path
        if path == "/redirect":
            self.send_response(301)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/chain":
            self.send_response(302)
            self.send_header("Location", "/redirect")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/domainhop":
            # Cannot hop hosts on a loopback server; emulate the auditor's
            # domain-change detection directly in state instead (see tests).
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/gone":
            self.send_response(410)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/missing":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/rate":
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/forbidden":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/nohead":
            if head:
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                body = b"x"
                self.send_response(200)
                self.send_header("Content-Length", "1")
                self.end_headers()
                if not head:
                    self.wfile.write(body)
        elif path == "/timeout":
            # Never respond; the client timeout exercises the net- path.
            time_sleep(5.0)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:  # /ok and anything else
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args) -> None:  # silence
        pass


def time_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


class AuditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sources = self.root / "sources.jsonl"
        self.state_dir = self.root / "state"
        self.report = self.root / "report.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def base(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def write_sources(self, *paths: str) -> None:
        lines = [
            json.dumps({
                "jurisdiction_id": f"jurisdictions/TJUR-{i:04d}",
                "jurisdiction": "Test",
                "source_type": "regulator",
                "title": f"src {i}",
                "url": self.base(path),
            })
            for i, path in enumerate(paths, start=1)
        ]
        self.sources.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run_audit(self, *extra: str) -> int:
        return main([
            "--sources", str(self.sources),
            "--state-dir", str(self.state_dir),
            "--report", str(self.report),
            "--min-delay", "0",
            "--timeout", "1",
            *extra,
        ])

    def read_report(self) -> dict:
        return json.loads(self.report.read_text(encoding="utf-8"))

    # -- classification ----------------------------------------------------

    def test_classify_cause_transient_vs_dead(self) -> None:
        self.assertEqual(classify_cause("http-429"), "transient")
        self.assertEqual(classify_cause("http-403"), "transient")
        self.assertEqual(classify_cause("http-503"), "transient")
        self.assertEqual(classify_cause("http-404"), "dead")
        self.assertEqual(classify_cause("http-410"), "dead")
        self.assertEqual(classify_cause("http-401"), "suspect")
        self.assertEqual(classify_cause("net-URLError"), "dead")
        self.assertEqual(classify_cause("net-TimeoutError"), "transient")

    # -- live probes against the loopback server ---------------------------

    def test_ok_and_cache_skip(self) -> None:
        self.write_sources("/ok")
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        report = self.read_report()
        self.assertEqual(report["summary"]["ok"], 1)
        self.assertEqual(report["urls_checked"], 1)
        # Second run: cached OK suppresses the probe.
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_report()["urls_checked"], 0)
        self.assertEqual(self.read_report()["urls_cache_skipped"], 1)

    def test_redirect_followed_and_recorded(self) -> None:
        self.write_sources("/chain")
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        url_row = self.read_report()["urls"][0]
        self.assertEqual(url_row["status"], "ok")
        self.assertEqual(url_row["redirected_to"], self.base("/ok"))
        self.assertFalse(url_row["domain_changed"])

    def test_head_rejected_get_fallback(self) -> None:
        self.write_sources("/nohead")
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_report()["summary"]["ok"], 1)

    def test_404_is_failing_new_then_dead_after_window(self) -> None:
        self.write_sources("/missing")
        rc = self.run_audit()
        self.assertEqual(rc, 0)  # inside confirm window: not confirmed dead
        row = self.read_report()["urls"][0]
        self.assertEqual(row["status"], "failing-new")
        # Age the failure past the window by rewinding state.
        state_path = self.state_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        url = row["url"]
        state["urls"][url]["first_seen_bad"] = parse_iso("2020-01-01T00:00:00Z").strftime("%Y-%m-%dT%H:%M:%SZ")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        rc = self.run_audit()
        self.assertEqual(rc, 1)  # confirmed dead -> exit 1
        self.assertEqual(self.read_report()["urls"][0]["status"], "dead")

    def test_429_is_transient_not_death(self) -> None:
        self.write_sources("/rate")
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        row = self.read_report()["urls"][0]
        self.assertEqual(row["status"], "transient")
        # Even aged past the window, 429 never confirms dead.
        state_path = self.state_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["urls"][row["url"]]["first_seen_bad"] = "2020-01-01T00:00:00Z"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_report()["urls"][0]["status"], "transient")

    def test_403_is_transient(self) -> None:
        self.write_sources("/forbidden")
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_report()["summary"]["transient"], 1)

    def test_timeout_is_transient(self) -> None:
        self.write_sources("/timeout")
        rc = self.run_audit("--timeout", "0.3")
        self.assertEqual(rc, 0)
        row = self.read_report()["urls"][0]
        self.assertEqual(row["status"], "transient")

    def test_410_dead_only_after_window(self) -> None:
        self.write_sources("/gone")
        rc = self.run_audit()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_report()["urls"][0]["status"], "failing-new")

    def test_no_network_mode_reports_from_state(self) -> None:
        self.write_sources("/ok", "/missing")
        self.run_audit()
        rc = self.run_audit("--no-network")
        self.assertEqual(rc, 0)
        report = self.read_report()
        self.assertEqual(report["urls_checked"], 0)
        self.assertEqual(report["summary"]["ok"], 1)
        self.assertEqual(report["summary"]["failing-new"], 1)

    def test_domain_change_flagged(self) -> None:
        # Emulate a redirect to another host via state (loopback server
        # cannot hop domains): exercise the report-side domain_changed logic.
        self.write_sources("/ok")
        self.run_audit()
        state_path = self.state_dir / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        url = self.base("/ok")
        state["urls"][url]["redirected_to"] = "https://example.org/new-home"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.run_audit("--no-network")
        row = self.read_report()["urls"][0]
        self.assertTrue(row["domain_changed"])
        self.assertEqual(row["redirected_to"], "https://example.org/new-home")

    # -- input validation ----------------------------------------------------

    def test_duplicate_url_rejected(self) -> None:
        self.sources.write_text(
            json.dumps({"url": self.base("/ok"), "source_type": "regulator"}) + "\n"
            + json.dumps({"url": self.base("/ok"), "source_type": "regulator"}) + "\n",
            encoding="utf-8",
        )
        rc = self.run_audit()
        self.assertEqual(rc, 2)

    def test_missing_sources_file_is_error(self) -> None:
        rc = main([
            "--sources", str(self.root / "nope.jsonl"),
            "--state-dir", str(self.state_dir),
            "--report", str(self.report),
        ])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
