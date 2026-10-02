"""Tests for the syndication feed generator (scripts/generate_feeds.py)."""

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from scripts import generate_feeds as gf  # noqa: E402

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def write_record(root: Path, collection: str, form_id: str, *, title=None,
                 status="published", summary=None, date=None, body=""):
    col = root / collection
    col.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"id: {collection}/{form_id}"]
    if title:
        fm.append(f'title: "{title}"')
    if status:
        fm.append(f"status: {status}")
    if summary:
        fm.append(f'summary: "{summary}"')
    if date:
        fm.append(f"date: {date}")
    fm += ["---", ""]
    (col / f"{form_id}.md").write_text("\n".join(fm) + body, encoding="utf-8")


class TestDateExtraction(unittest.TestCase):
    """The three agencies' table formats plus frontmatter date precedence."""

    def _body_with(self, row):
        return "\n# Heading\n\n" + row + "\n"

    def test_dcc_slash_format(self):
        body = self._body_with("| **DCC Recall Publication Date** | 8/3/2026 |")
        self.assertEqual(gf.extract_date("", body).strftime("%Y-%m-%d"), "2026-08-03")

    def test_cra_iso_format(self):
        body = self._body_with("| Publication date | 2025-09-02 |")
        self.assertEqual(gf.extract_date("", body).strftime("%Y-%m-%d"), "2025-09-02")

    def test_cpsc_month_name_format(self):
        body = self._body_with("| **Recall date** | September 18, 2025 |")
        self.assertEqual(gf.extract_date("", body).strftime("%Y-%m-%d"), "2025-09-18")

    def test_advisory_iso_format(self):
        body = self._body_with("| Advisory date | 2025-02-03 |")
        self.assertEqual(gf.extract_date("", body).strftime("%Y-%m-%d"), "2025-02-03")

    def test_no_date_yields_none(self):
        self.assertIsNone(gf.extract_date("", "# Just prose\n\nNo tables here."))

    def test_frontmatter_date_wins_over_body(self):
        body = self._body_with("| **DCC Recall Publication Date** | 1/2/2020 |")
        got = gf.extract_date("2026-08-04", body)
        self.assertEqual(got.strftime("%Y-%m-%d"), "2026-08-04")

    def test_frontmatter_date_accepts_rfc3339_and_z(self):
        self.assertEqual(gf.parse_frontmatter_date("2026-08-04").strftime("%Y-%m-%d"), "2026-08-04")
        self.assertEqual(gf.parse_frontmatter_date("2026-08-04T10:20:30Z").strftime("%H:%M:%S"), "10:20:30")
        self.assertEqual(gf.parse_frontmatter_date("2026-08-04T10:20:30+02:00").strftime("%H:%M:%S"), "10:20:30")
        self.assertIsNone(gf.parse_frontmatter_date("not a date"))

    def test_unparseable_frontmatter_date_falls_back_to_body(self):
        body = self._body_with("| Publication date | 2025-09-02 |")
        got = gf.extract_date("whenever", body)
        self.assertEqual(got.strftime("%Y-%m-%d"), "2025-09-02")


class TestCollect(unittest.TestCase):
    def test_default_status_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_record(root, "recalls", "TRCL-9999", title="No status line", status=None)
            items = gf.collect("recalls", root)
            self.assertEqual(items, [])

    def test_draft_and_unpublished_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_record(root, "recalls", "TRCL-0001", status="draft",
                         body="\n| Publication date | 2025-01-01 |\n")
            write_record(root, "recalls", "TRCL-0002", status="unpublished",
                         body="\n| Publication date | 2025-01-02 |\n")
            self.assertEqual(gf.collect("recalls", root), [])

    def test_sorting_dated_newest_first_undated_by_id_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_record(root, "recalls", "TRCL-0002",
                         body="\n| Publication date | 2025-01-01 |")
            write_record(root, "recalls", "TRCL-0001",
                         body="\n| Publication date | 2026-01-01 |")
            write_record(root, "recalls", "TRCL-0009")  # undated
            write_record(root, "recalls", "TRCL-0004")  # undated
            ids = [i["form_id"] for i in gf.collect("recalls", root)]
            self.assertEqual(ids, ["TRCL-0001", "TRCL-0002", "TRCL-0004", "TRCL-0009"])


class TestEndToEnd(unittest.TestCase):
    def _corpus(self, root: Path):
        # Two dated recalls, one dated advisory, one undated changelog.
        write_record(root, "recalls", "TRCL-0002", title="Older & <Real>",
                     summary='Recall with "quotes" & amps',
                     body="\n\nOlder recall prose.\n\n| **DCC Recall Publication Date** | 02/11/2025 |\n")
        write_record(root, "recalls", "TRCL-0001", title="Newer",
                     body="\n\nNewer recall prose.\n\n| **DCC Recall Publication Date** | 08/03/2026 |\n")
        write_record(root, "safety-advisories", "TSAD-0001", title="Advisory",
                     body="\n\nAdvisory prose.\n\n| Advisory date | 2025-08-06 |\n")
        write_record(root, "changelog", "TCHG-0001", title="Undated change",
                     body="\n\nChangelog entry prose.\n")

    def _run(self, root: Path, out: Path, extra=()):
        import sys as _sys
        argv = _sys.argv
        _sys.argv = ["generate_feeds.py", "--content", str(root),
                     "--output", str(out), "--site-url", "https://example.com", *extra]
        try:
            rc = gf.main()
        finally:
            _sys.argv = argv
        return rc

    def test_feeds_content_and_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "content", Path(tmp) / "out"
            self._corpus(root)
            rc = self._run(root, out)
            self.assertEqual(rc, 0)

            # RSS: dated items only, escaped, sorted, counts asserted.
            r = ET.parse(out / "recalls.xml").getroot()
            items = r.find("channel").findall("item")
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0].findtext("title"), "Newer")  # newest first
            self.assertEqual(items[1].findtext("title"), "Older & <Real>")
            self.assertEqual(items[1].findtext("description"), 'Recall with "quotes" & amps')
            a = ET.parse(out / "safety-advisories.xml").getroot()
            self.assertEqual(len(a.find("channel").findall("item")), 1)

            # Atom: combined includes the undated changelog entry.
            f = ET.parse(out / "feed.xml").getroot()
            entries = f.findall(ATOM_NS + "entry")
            self.assertEqual(len(entries), 4)
            ids = [e.findtext(ATOM_NS + "id") for e in entries]
            self.assertIn("https://example.com/changelog/TCHG-0001.html", ids)
            # RFC 4287 author MUST: feed-level author present.
            self.assertIsNotNone(f.find(ATOM_NS + "author"))
            # Undated entry carries the stable epoch, not build time.
            undated = [e for e in entries if e.findtext(ATOM_NS + "id").endswith("TCHG-0001.html")]
            self.assertEqual(undated[0].findtext(ATOM_NS + "updated"), "1970-01-01T00:00:00Z")
            # Dated entries newest-first.
            ups = [e.findtext(ATOM_NS + "updated") for e in entries
                   if not e.findtext(ATOM_NS + "id").endswith("TCHG-0001.html")]
            self.assertEqual(ups, sorted(ups, reverse=True))

    def test_deterministic_content_across_runs(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            self._corpus(root)
            out1, out2 = Path(tmp) / "o1", Path(tmp) / "o2"
            self._run(root, out1)
            time.sleep(1.1)  # let the build-stamp second tick over
            self._run(root, out2)

            def stable(name, text):
                # Drop only the generation-time build stamps; everything
                # else must be byte-identical across runs.
                lines = text.splitlines()
                if name == "feed.xml":
                    return [l for l in lines if not l.startswith("  <updated>")]
                return [l for l in lines if not l.lstrip().startswith("<lastBuildDate>")]

            for name in ("recalls.xml", "safety-advisories.xml", "feed.xml"):
                a = stable(name, (out1 / name).read_text())
                b = stable(name, (out2 / name).read_text())
                self.assertEqual(a, b, f"{name}: content changed between runs")
            # The dropped stamps really did tick (test tests something).
            self.assertNotEqual((out1 / "feed.xml").read_text(), (out2 / "feed.xml").read_text())

    def test_validator_rejects_missing_author(self):
        # The RFC 4287 MUST must actually be enforced: strip the author
        # element and the validator has to notice.
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "content", Path(tmp) / "out"
            self._corpus(root)
            self._run(root, out)
            text = (out / "feed.xml").read_text()
            (out / "feed.xml").write_text(text.replace("  <author><name>Thermal Extraction Devices Archive</name></author>\n", ""))
            with self.assertRaises(AssertionError):
                gf.validate_outputs(out, {"recalls.xml": 2, "safety-advisories.xml": 1, "feed.xml": 4})

    def test_validator_rejects_wrong_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "content", Path(tmp) / "out"
            self._corpus(root)
            self._run(root, out)
            with self.assertRaises(AssertionError):
                gf.validate_outputs(out, {"recalls.xml": 99, "safety-advisories.xml": 1, "feed.xml": 4})

    def test_limit_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "content", Path(tmp) / "out"
            self._corpus(root)
            self._run(root, out, ["--limit", "1"])
            r = ET.parse(out / "recalls.xml").getroot()
            self.assertEqual(len(r.find("channel").findall("item")), 1)
            f = ET.parse(out / "feed.xml").getroot()
            self.assertEqual(len(f.findall(ATOM_NS + "entry")), 1)


if __name__ == "__main__":
    unittest.main()
