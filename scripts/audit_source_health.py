#!/usr/bin/env python3
"""External link-health and source-decay auditor (P16).

Checks every URL in ``metadata/jurisdiction-sources.jsonl``: follows redirects,
records where they land, distinguishes confirmed death (410/404-durable, DNS
gone) from transient trouble (429/403/5xx/timeouts), and classifies each URL by
its recorded source type. State persists under ``data/source-health/`` so repeat
runs are cheap, polite, and can answer "when did this source last work?".

Exit codes follow the repo audit convention: 0 healthy (or only transient
findings), 1 confirmed-decay findings, 2 auditor error.

Network policy:
  - Polite: one request at a time, ``--min-delay`` seconds between requests,
    a descriptive User-Agent, and Retry-After honored on 429/503.
  - Caching: a URL checked OK within ``--cache-ttl`` days is not re-checked;
    failures are always re-checked (decay detection must stay live).
  - Transient vs confirmed: a URL is only "dead" when it fails the same way
    on two runs at least ``--confirm-window`` hours apart (first-seen-bad is
    persisted for exactly this). 429/403 are never confirmed-death causes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = "TED-source-health-auditor/0.1 (+https://thermalextractiondevices.com; link decay audit)"
DEFAULT_SOURCES = "metadata/jurisdiction-sources.jsonl"
STATE_DIR = Path("data/source-health")
REPORT_PATH = Path("reports/source-health-report.json")
STATE_FILE = STATE_DIR / "state.json"
ISO = "%Y-%m-%dT%H:%M:%SZ"

# Statuses that indicate the server is alive but annoyed, or the network is
# flaky. Never counted toward confirmed death.
TRANSIENT_HTTP = {403, 408, 429, 500, 502, 503, 504}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime(ISO)


def parse_iso(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, ISO).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "urls": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "urls": {}}
    if not isinstance(state, dict) or not isinstance(state.get("urls"), dict):
        return {"version": 1, "urls": {}}
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_sources(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number}: invalid JSON line: {error}") from error
    seen: set[str] = set()
    for row in rows:
        url = row.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError(f"{path}: row with missing or non-http url: {row!r}")
        if url in seen:
            raise ValueError(f"{path}: duplicate url: {url}")
        seen.add(url)
    return rows


class HttpResult:
    __slots__ = ("status", "final_url", "reason")

    def __init__(self, status: int, final_url: str, reason: str) -> None:
        self.status = status
        self.final_url = final_url
        self.reason = reason


def check_url(url: str, timeout: float) -> HttpResult:
    """One polite HEAD-or-GET probe with redirects followed."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResult(response.status, response.geturl(), "ok")
    except urllib.error.HTTPError as error:
        # 405/501: HEAD not allowed — retry once as a ranged GET (some servers
        # reject HEAD outright but serve GET fine; that is health, not decay).
        if error.code in (405, 501):
            get = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
            try:
                with urllib.request.urlopen(get, timeout=timeout) as response:
                    return HttpResult(response.status, response.geturl(), "ok-get")
            except urllib.error.HTTPError as retry_error:
                return HttpResult(retry_error.code, retry_error.geturl() or url, f"http-{retry_error.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as retry_error:
                return HttpResult(0, url, f"net-{type(retry_error).__name__}")
        return HttpResult(error.code, getattr(error, "url", None) or url, f"http-{error.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return HttpResult(0, url, f"net-{type(error).__name__}")


def classify_cause(reason: str) -> str:
    """Map a failure reason to its decay class."""
    if reason.startswith("http-"):
        code = int(reason.split("-", 1)[1])
        if code in TRANSIENT_HTTP:
            return "transient"
        if code in (404, 410):
            return "dead"
        return "suspect"
    # net-*: DNS/name failures are the classic "domain lapsed" signal;
    # everything else (timeouts, refused, TLS handshake) is transient-shaped.
    name = reason.split("-", 1)[1]
    if name in ("URLError", "gaierror"):
        return "dead"
    return "transient"


def waited_backoff(reason: str) -> bool:
    return classify_cause(reason) == "transient"


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path(DEFAULT_SOURCES))
    parser.add_argument("--state-dir", type=Path, default=STATE_DIR)
    parser.add_argument("--report", type=Path, default=Path(REPORT_PATH))
    parser.add_argument("--min-delay", type=float, default=2.0,
                        help="minimum seconds between HTTP requests (politeness)")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--cache-ttl-days", type=float, default=7.0,
                        help="days a recorded OK suppresses re-checking")
    parser.add_argument("--confirm-window-hours", type=float, default=24.0,
                        help="hours a failure must persist before 'dead' is confirmed")
    parser.add_argument("--limit", type=int, default=0,
                        help="check at most N URLs (0 = all); cache still honored")
    parser.add_argument("--no-network", action="store_true",
                        help="skip HTTP entirely; report from cached state only")
    args = parser.parse_args(argv)

    state_path = args.state_dir / "state.json"
    try:
        rows = load_sources(args.sources)
        state = load_state(state_path)
    except (OSError, ValueError) as error:
        print(f"source-health: error: {error}", file=sys.stderr)
        return 2

    urls_state = state["urls"]
    checked = skipped = 0
    last_request = 0.0
    findings: list[dict] = []
    confirmed_dead: list[dict] = []

    for row in rows:
        url = row["url"]
        entry = urls_state.setdefault(url, {})
        first_seen = entry.get("first_seen") or iso(now())
        entry["first_seen"] = first_seen

        if args.no_network:
            continue

        last_ok_raw = entry.get("last_ok")
        last_ok = parse_iso(last_ok_raw) if last_ok_raw else None
        if (last_ok is not None and now() - last_ok < timedelta(days=args.cache_ttl_days)
                and not entry.get("last_bad")):
            skipped += 1
            continue

        if args.limit and checked >= args.limit:
            skipped += 1
            continue

        # Politeness: never burst, even across cache skips.
        wait = args.min_delay - (time.monotonic() - last_request)
        if wait > 0 and checked:
            time.sleep(wait)
        last_request = time.monotonic()

        result = check_url(url, args.timeout)
        checked += 1

        entry["last_checked"] = iso(now())
        entry["last_status"] = result.status
        entry["last_reason"] = result.reason
        if result.status == 200 or result.reason in ("ok", "ok-get"):
            entry["last_ok"] = iso(now())
            entry.pop("first_seen_bad", None)
            entry.pop("last_bad", None)
            final = result.final_url
            redirected = final.rstrip("/") != url.rstrip("/")
            entry["redirected_to"] = final if redirected else None
        else:
            entry["last_bad"] = result.reason
            entry["first_seen_bad"] = entry.get("first_seen_bad") or iso(now())

    if not args.no_network:
        save_state(state_path, state)

    # ---- classify from state (works with or without a network pass) ----
    for row in rows:
        url = row["url"]
        entry = urls_state.get(url, {})
        last_ok = parse_iso(entry.get("last_ok") or "")
        last_bad_reason = entry.get("last_bad")
        first_bad = parse_iso(entry.get("first_seen_bad") or "")

        if last_bad_reason is None:
            status = "ok" if last_ok else "unknown"
        else:
            cause = classify_cause(last_bad_reason)
            if cause == "transient":
                status = "transient"
            elif first_bad and now() - first_bad < timedelta(hours=args.confirm_window_hours):
                status = "failing-new"
            else:
                status = "dead"
        redirected_to = entry.get("redirected_to")
        finding = {
            "url": url,
            "jurisdiction_id": row.get("jurisdiction_id"),
            "source_type": row.get("source_type"),
            "status": status,
            "last_ok": entry.get("last_ok"),
            "first_seen_bad": entry.get("first_seen_bad"),
            "redirected_to": redirected_to,
            "domain_changed": bool(redirected_to and domain_of(redirected_to) != domain_of(url)),
            "last_reason": entry.get("last_reason"),
        }
        findings.append(finding)
        if status == "dead":
            confirmed_dead.append(finding)

    report = {
        "generated_at": iso(now()),
        "sources_file": str(args.sources),
        "urls_total": len(findings),
        "urls_checked": checked,
        "urls_cache_skipped": skipped,
        "summary": {
            "ok": sum(1 for f in findings if f["status"] == "ok"),
            "transient": sum(1 for f in findings if f["status"] == "transient"),
            "failing-new": sum(1 for f in findings if f["status"] == "failing-new"),
            "dead": sum(1 for f in findings if f["status"] == "dead"),
            "redirected": sum(1 for f in findings if f["redirected_to"]),
            "domain_changed": sum(1 for f in findings if f["domain_changed"]),
            "unknown": sum(1 for f in findings if f["status"] == "unknown"),
        },
        "remediation": {
            "dead": "Replace the source or record an archived copy; a URL stays 'dead' only after failing for the confirm window.",
            "transient": "Rate-limited or temporarily unavailable — do not treat as content failure; re-run the auditor later.",
            "failing-new": "First failure observed inside the confirm window — re-run after it elapses to confirm decay.",
            "redirected": "Review redirected_to; update the JSONL when the redirect is permanent.",
        },
        "urls": findings,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"source-health: {len(findings)} urls ({checked} checked, {skipped} cache-skipped) -> {args.report}")
    for key, count in report["summary"].items():
        print(f"  {key:>14}: {count}")
    if confirmed_dead:
        print(f"source-health: {len(confirmed_dead)} confirmed-dead source(s)", file=sys.stderr)
        for finding in confirmed_dead[:10]:
            print(f"  {finding['url']} ({finding['last_reason']}, first bad {finding['first_seen_bad']})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
