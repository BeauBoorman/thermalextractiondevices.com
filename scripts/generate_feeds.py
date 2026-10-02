#!/usr/bin/env python3
"""Generate RSS 2.0 and Atom feeds for time-ordered TED collections.

Reads content/recalls/, content/safety-advisories/, and content/changelog/
and writes, under the directory given by --output:

  recalls.xml           RSS 2.0  (recalls)
  safety-advisories.xml RSS 2.0  (safety advisories)
  feed.xml              Atom 1.0 (combined: recalls + advisories + changelog)

Item URLs follow the Boris output layout (<collection>/<FORM-ID>.html,
verified against the compiled site). Dates are RFC 822 (RSS) / RFC 3339
(Atom). A record's source date comes from its `date:` frontmatter (ISO
8601) when present, else from its rendered body fact tables; records with
neither are omitted from the dated RSS feeds rather than guessed (no
fabricated pubDates). The combined Atom feed includes undated records
(changelog today) ordered after the dated ones, carrying a stable
1970-01-01 epoch `updated` so rebuilds never re-date them. A feed-level
atom:author satisfies the RFC 4287 author MUST.

The script validates its own output with xml.etree and spec-shape
assertions — including the Atom author MUST, per-feed item-count
expectations, and newest-first ordering — before exiting 0.

Output is deterministic in content; lastBuildDate/atom:updated are stamped
at generation time, as feeds conventionally are. Reads only the content
tree; never touches the network.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

SITE_URL_DEFAULT = "https://thermalextractiondevices.com"
GENERATOR = "TED generate_feeds.py"
AUTHOR_NAME = "Thermal Extraction Devices Archive"

# Stable sentinel for undated Atom entries: real dates always sort after
# it, and rebuilds never re-date undated records (no phantom updates).
ATOM_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# ---------------------------------------------------------------- parsing

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
KV_RE = re.compile(r'^([A-Za-z_][\w-]*):\s*(.*)$', re.M)

# Frontmatter `date:` accepted formats (ISO date, or RFC 3339 with an
# explicit UTC designator or numeric offset).
FM_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z")

# Source-date extraction from the rendered body tables: (label regex,
# date format) pairs. Order matters only within one file; first match wins.
DATE_PATTERNS = [
    (re.compile(r"\|\s*\*{0,2}DCC Recall Publication Date\*{0,2}\s*\|\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})"), "%m/%d/%Y"),
    (re.compile(r"\|\s*\*{0,2}Publication date\*{0,2}\s*\|\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"), "%Y-%m-%d"),
    (re.compile(r"\|\s*\*{0,2}Recall date\*{0,2}\s*\|\s*([A-Z][a-z]+ [0-9]{1,2}, [0-9]{4})"), "%B %d, %Y"),
    (re.compile(r"\|\s*Advisory date\s*\|\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"), "%Y-%m-%d"),
]


def parse_frontmatter(text: str) -> dict:
    m = FM_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        kv = KV_RE.match(line)
        if kv:
            out[kv.group(1)] = kv.group(2).strip().strip('"')
    return out


def strip_quotes(value: str) -> str:
    return value.strip().strip('"')


def parse_frontmatter_date(value: str):
    """Parse a frontmatter `date:` value, or return None."""
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    for fmt in FM_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_date(fm_date: str, body: str):
    """Return the record's source date: frontmatter `date:` first, then the
    body fact tables. None when neither yields a parseable date."""
    if fm_date:
        parsed = parse_frontmatter_date(fm_date)
        if parsed:
            return parsed
    for pattern, fmt in DATE_PATTERNS:
        m = pattern.search(body)
        if not m:
            continue
        raw = m.group(1).strip()
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def first_paragraph(body: str) -> str:
    """Rough plain-text excerpt after the H1, tables and asides excluded."""
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("|") or s.startswith("<") or s.startswith("#"):
            continue
        if s.startswith("{{include"):
            continue
        lines.append(s)
        if sum(len(x) for x in lines) > 400:
            break
    text = " ".join(lines)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> text
    text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)  # emphasis
    return text.strip()


def collect(collection: str, content_root: Path):
    """Return item dicts for one collection: dated items newest-first, then
    undated items in form-ID order."""
    items = []
    col_dir = content_root / collection
    if not col_dir.is_dir():
        return items
    for md in sorted(col_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        # Default-closed: a record without an explicit published status
        # never rides a public feed.
        if fm.get("status") != "published":
            continue
        form_id = (fm.get("id") or "").split("/")[-1]
        if not form_id:
            continue
        body = text[FM_RE.match(text).end():] if FM_RE.match(text) else text
        items.append({
            "collection": collection,
            "form_id": form_id,
            "title": strip_quotes(fm.get("title") or form_id),
            "summary": strip_quotes(fm.get("summary") or "") or first_paragraph(body),
            "date": extract_date(fm.get("date", ""), body),
            "url_path": f"{collection}/{form_id}.html",
        })
    dated = [i for i in items if i["date"]]
    undated = sorted((i for i in items if not i["date"]), key=lambda i: i["form_id"])
    dated.sort(key=lambda i: (i["date"], i["form_id"]), reverse=True)
    # Undated items stay in ID order after the dated ones (Atom only).
    return dated + undated


def combine(*collections) -> list:
    """Merge collections for the combined feed: dated items newest-first,
    undated items in form-ID order after them."""
    items = [i for col in collections for i in col]
    dated = [i for i in items if i["date"]]
    undated = sorted((i for i in items if not i["date"]), key=lambda i: i["form_id"])
    dated.sort(key=lambda i: (i["date"], i["form_id"]), reverse=True)
    return dated + undated


# ------------------------------------------------------------- rendering

def xml_escape(value: str) -> str:
    # quote=True: values land in attribute contexts (href, rel) as well as
    # text nodes; &quot; is valid in both.
    return html.escape(value, quote=True)


def rfc822(dt: datetime) -> str:
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_rss(channel_title: str, site_url: str, self_path: str, description: str, items, limit: int) -> str:
    now = rfc822(datetime.now(timezone.utc))
    entries = []
    for item in [i for i in items if i["date"]][:limit]:
        url = f"{site_url.rstrip('/')}/{item['url_path']}"
        entries.append(
            "    <item>\n"
            f"      <title>{xml_escape(item['title'])}</title>\n"
            f"      <link>{xml_escape(url)}</link>\n"
            f"      <guid isPermaLink=\"true\">{xml_escape(url)}</guid>\n"
            f"      <pubDate>{rfc822(item['date'])}</pubDate>\n"
            f"      <description>{xml_escape(item['summary'])}</description>\n"
            "    </item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{xml_escape(channel_title)}</title>\n"
        f"    <link>{xml_escape(site_url)}</link>\n"
        f"    <description>{xml_escape(description)}</description>\n"
        f"    <language>en</language>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        f"    <generator>{xml_escape(GENERATOR)}</generator>\n"
        f"    <atom:link href=\"{xml_escape(site_url.rstrip('/') + '/' + self_path)}\" rel=\"self\" type=\"application/rss+xml\" />\n"
        + "\n".join(entries)
        + "\n  </channel>\n</rss>\n"
    )


def build_atom(feed_id: str, title: str, site_url: str, author: str, items, limit: int) -> str:
    now = rfc3339(datetime.now(timezone.utc))
    entries = []
    for item in items[:limit]:
        url = f"{site_url.rstrip('/')}/{item['url_path']}"
        updated = rfc3339(item["date"] if item["date"] else ATOM_EPOCH)
        entries.append(
            "  <entry>\n"
            f"    <title>{xml_escape(item['title'])}</title>\n"
            f"    <id>{xml_escape(url)}</id>\n"
            f"    <link rel=\"alternate\" type=\"text/html\" href=\"{xml_escape(url)}\" />\n"
            f"    <updated>{updated}</updated>\n"
            f"    <summary>{xml_escape(item['summary'])}</summary>\n"
            "  </entry>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{xml_escape(title)}</title>\n"
        f"  <id>{xml_escape(feed_id)}</id>\n"
        f"  <link rel=\"alternate\" type=\"text/html\" href=\"{xml_escape(site_url)}\" />\n"
        f"  <link rel=\"self\" href=\"{xml_escape(feed_id)}\" />\n"
        f"  <updated>{now}</updated>\n"
        f"  <author><name>{xml_escape(author)}</name></author>\n"
        f"  <generator>{xml_escape(GENERATOR)}</generator>\n"
        + "\n".join(entries)
        + "\n</feed>\n"
    )


# ------------------------------------------------------------- validation

def _assert_non_increasing(values: list, label: str):
    for prev, cur in zip(values, values[1:]):
        assert prev >= cur, f"{label}: not newest-first ({prev} < {cur})"


def validate_outputs(output_dir: Path, expected_counts: dict) -> int:
    """Parse each generated file and assert spec-required shape (RSS 2.0
    required channel/item elements, RFC 822 pubDates, newest-first order;
    Atom 1.0 required feed/entry elements, the RFC 4287 author MUST,
    RFC 3339 timestamps, expected item counts, newest-first order).
    Returns the number of files checked."""
    checked = 0
    for name in ("recalls.xml", "safety-advisories.xml"):
        path = output_dir / name
        root = ET.parse(path).getroot()
        assert root.tag == "rss" and root.get("version") == "2.0", f"{name}: not RSS 2.0"
        channel = root.find("channel")
        assert channel is not None, f"{name}: missing channel"
        for tag in ("title", "link", "description"):
            assert channel.findtext(tag), f"{name}: channel missing {tag}"
        items = channel.findall("item")
        dates = []
        for item in items:
            for tag in ("title", "link", "guid", "pubDate", "description"):
                assert item.findtext(tag), f"{name}: item missing {tag}"
            dates.append(datetime.strptime(item.findtext("pubDate"), "%a, %d %b %Y %H:%M:%S GMT"))
        _assert_non_increasing(dates, name)
        assert len(items) == expected_counts[name], (
            f"{name}: expected {expected_counts[name]} items, found {len(items)}")
        checked += 1
    path = output_dir / "feed.xml"
    root = ET.parse(path).getroot()
    ns = "{http://www.w3.org/2005/Atom}"
    assert root.tag == ns + "feed", "feed.xml: not Atom 1.0"
    for tag in ("title", "id", "updated"):
        assert root.findtext(ns + tag), f"feed.xml: missing {tag}"
    # RFC 4287 §4.1.1: a feed MUST contain an atom:author unless every
    # entry does.
    feed_author = root.find(ns + "author")
    entries = root.findall(ns + "entry")
    assert feed_author is not None or all(e.find(ns + "author") is not None for e in entries), (
        "feed.xml: no atom:author at feed level and not every entry has one (RFC 4287 §4.1.1)")
    updateds = []
    for entry in entries:
        for tag in ("title", "id", "updated"):
            assert entry.findtext(ns + tag), f"feed.xml: entry missing {tag}"
        assert entry.find(ns + "link") is not None, "feed.xml: entry missing link"
        updateds.append(datetime.strptime(entry.findtext(ns + "updated"), "%Y-%m-%dT%H:%M:%SZ"))
    _assert_non_increasing(updateds, "feed.xml")
    assert len(entries) == expected_counts["feed.xml"], (
        f"feed.xml: expected {expected_counts['feed.xml']} entries, found {len(entries)}")
    checked += 1
    return checked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--content", default="content", help="content root (default: content)")
    ap.add_argument("--output", required=True, help="output directory for feed XML files")
    ap.add_argument("--site-url", default=SITE_URL_DEFAULT)
    ap.add_argument("--limit", type=int, default=50, help="max items per feed")
    args = ap.parse_args()

    content_root = Path(args.content)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    site = args.site_url.rstrip("/")

    recalls = collect("recalls", content_root)
    advisories = collect("safety-advisories", content_root)
    changelog = collect("changelog", content_root)
    combined = combine(recalls, advisories, changelog)

    (out_dir / "recalls.xml").write_text(
        build_rss(
            "Thermal Extraction Devices — Recalls",
            site,
            "recalls.xml",
            "Cannabis product and device recall notices tracked by the Thermal Extraction Devices archive.",
            recalls,
            args.limit,
        ),
        encoding="utf-8",
    )
    (out_dir / "safety-advisories.xml").write_text(
        build_rss(
            "Thermal Extraction Devices — Safety Advisories",
            site,
            "safety-advisories.xml",
            "Public health and safety advisories tracked by the Thermal Extraction Devices archive.",
            advisories,
            args.limit,
        ),
        encoding="utf-8",
    )
    (out_dir / "feed.xml").write_text(
        build_atom(
            f"{site}/feed.xml",
            "Thermal Extraction Devices — Updates",
            site,
            AUTHOR_NAME,
            combined,
            args.limit,
        ),
        encoding="utf-8",
    )

    dated = {
        "recalls": sum(1 for i in recalls if i["date"]),
        "advisories": sum(1 for i in advisories if i["date"]),
        "changelog": sum(1 for i in changelog if i["date"]),
    }
    combined_dated = sum(1 for i in combined if i["date"])
    expected_counts = {
        "recalls.xml": min(dated["recalls"], args.limit),
        "safety-advisories.xml": min(dated["advisories"], args.limit),
        "feed.xml": min(len(combined), args.limit),
    }
    checked = validate_outputs(out_dir, expected_counts)
    print(f"generate_feeds: wrote recalls.xml ({dated['recalls']} dated), "
          f"safety-advisories.xml ({dated['advisories']} dated), "
          f"feed.xml ({len(combined)} combined: {combined_dated} dated + "
          f"{len(combined) - combined_dated} undated, "
          f"changelog {dated['changelog']} dated); validated {checked} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
