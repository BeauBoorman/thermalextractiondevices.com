"""HTTP retrieval with retries, content-type guards, and streaming downloads.

A :class:`Fetcher` talks to the network. A :class:`FixtureFetcher` serves the
same interface from committed fixtures so unit tests never need a network.
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .core import ContentTypeError, IngestError

DEFAULT_USER_AGENT = (
    "ThermalExtractionDevices-Ingest/0.1 (+https://thermalextractiondevices.com)"
)

# Content types that may legitimately carry tabular payloads regardless of
# the advertised subtype.
_GENERIC_TYPES = {"application/octet-stream", "binary/octet-stream", "text/plain"}
_HTML_TYPES = {"text/html", "application/xhtml+xml"}


@dataclass
class FetchResult:
    """Metadata about one retrieved payload."""

    url: str
    content_type: str
    size_bytes: int
    sha256: str
    path: Optional[Path] = None
    retrieved_at: str = ""
    status_code: int = 200
    last_modified: Optional[str] = None
    data: Optional[bytes] = None


def _content_type_ok(declared: str, accepted: tuple[str, ...], *, allow_html: bool = False) -> bool:
    declared = declared.split(";", 1)[0].strip().lower()
    if declared in _HTML_TYPES:
        if allow_html:
            return True
        raise ContentTypeError(
            f"endpoint returned HTML ({declared!r}); expected one of {accepted}"
        )
    if declared in _GENERIC_TYPES:
        # Generic binary/plain: trust the URL extension, but still reject HTML.
        return True
    for prefix in accepted:
        if declared.startswith(prefix):
            return True
    return False


class Fetcher:
    """Small urllib-based fetcher with retries and content-type guards."""

    def __init__(
        self,
        *,
        retries: int = 3,
        backoff_seconds: float = 1.5,
        timeout: float = 60.0,
        user_agent: str = DEFAULT_USER_AGENT,
        accepted_types: tuple[str, ...] = ("text/csv", "application/csv", "application/json"),
        allow_html: bool = False,
        progress: Optional[Callable[[int, int], None]] = None,
    ):
        self.retries = max(1, retries)
        self.backoff = backoff_seconds
        self.timeout = timeout
        self.user_agent = user_agent
        self.accepted_types = accepted_types
        self.allow_html = allow_html
        self.progress = progress

    def with_accepted_types(self, *types: str) -> "Fetcher":
        """Return a copy of this fetcher accepting additional content types.

        State adapters with non-tabular official payloads (e.g. Nevada's
        lab-library ZIP archives) extend the guard instead of bypassing it.
        """
        return Fetcher(
            retries=self.retries, backoff_seconds=self.backoff,
            timeout=self.timeout, user_agent=self.user_agent,
            accepted_types=tuple(dict.fromkeys(self.accepted_types + tuple(types))),
            allow_html=self.allow_html, progress=self.progress,
        )

    def _open(self, url: str):
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        return urllib.request.urlopen(request, timeout=self.timeout)

    def _open_with_retries(self, url: str):
        last_error: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                return self._open(url)
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code in (404, 400, 401, 403):
                    raise IngestError(f"GET {url} -> HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
            if attempt < self.retries - 1:
                time.sleep(self.backoff * (2 ** attempt))
        raise IngestError(f"GET {url} failed after {self.retries} attempts: {last_error}")

    def fetch_bytes(self, url: str, max_bytes: Optional[int] = None) -> FetchResult:
        """Fetch a full payload into memory (small/medium files only)."""
        with self._open_with_retries(url) as response:
            content_type = response.headers.get("Content-Type", "")
            if not _content_type_ok(content_type, self.accepted_types, allow_html=self.allow_html):
                raise ContentTypeError(
                    f"GET {url}: content-type {content_type!r} not accepted "
                    f"(expected {self.accepted_types})"
                )
            data = response.read()
            if max_bytes is not None and len(data) > max_bytes:
                raise IngestError(f"GET {url}: payload exceeds {max_bytes} bytes")
            sha = hashlib.sha256(data).hexdigest()
            return FetchResult(
                url=url,
                content_type=content_type.split(";", 1)[0].strip(),
                size_bytes=len(data),
                sha256=sha,
                status_code=getattr(response, "status", 200),
                last_modified=response.headers.get("Last-Modified"),
                data=data,
            )

    def download(
        self,
        url: str,
        destination: Path,
        *,
        chunk_size: int = 1 << 20,
        max_bytes: Optional[int] = None,
    ) -> FetchResult:
        """Stream a potentially large payload to ``destination``.

        Writes to a ``.part`` file first so a failure never leaves a
        half-written snapshot at the final path.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        digest = hashlib.sha256()
        total = 0
        try:
            with self._open_with_retries(url) as response:
                content_type = response.headers.get("Content-Type", "")
                if not _content_type_ok(content_type, self.accepted_types, allow_html=self.allow_html):
                    raise ContentTypeError(
                        f"GET {url}: content-type {content_type!r} not accepted "
                        f"(expected {self.accepted_types})"
                    )
                with open(part, "wb") as out:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
                        if max_bytes is not None and total > max_bytes:
                            raise IngestError(
                                f"GET {url}: payload exceeds {max_bytes} bytes"
                            )
                        if self.progress:
                            self.progress(total, 0)
                last_modified = response.headers.get("Last-Modified")
        except Exception:
            part.unlink(missing_ok=True)
            raise
        part.rename(destination)
        return FetchResult(
            url=url,
            content_type=content_type.split(";", 1)[0].strip(),
            size_bytes=total,
            sha256=digest.hexdigest(),
            path=destination,
            status_code=200,
            last_modified=last_modified,
        )

    def fetch_text(self, url: str, encoding: str = "utf-8") -> str:
        result = self.fetch_bytes(url)
        if result.data is None:
            raise IngestError(f"fetch_bytes did not retain payload for {url}")
        return result.data.decode(encoding, errors="replace")

    def probe(self, url: str, sample_bytes: int = 8192) -> FetchResult:
        """Open a URL, read a small sample, then close immediately.

        Verifies an endpoint resolves and serves the expected content type
        without downloading the whole payload. Some servers ignore Range
        requests and would otherwise stream multi-hundred-MB files; reading a
        bounded sample and closing avoids that.
        """
        with self._open_with_retries(url) as response:
            content_type = response.headers.get("Content-Type", "")
            if not _content_type_ok(content_type, self.accepted_types, allow_html=self.allow_html):
                raise ContentTypeError(
                    f"GET {url}: content-type {content_type!r} not accepted "
                    f"(expected {self.accepted_types})"
                )
            data = response.read(sample_bytes)
            return FetchResult(
                url=url,
                content_type=content_type.split(";", 1)[0].strip(),
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                status_code=getattr(response, "status", 200),
                last_modified=response.headers.get("Last-Modified"),
                data=data,
            )


def decode_utf8_sig(data: bytes) -> str:
    """Decode UTF-8, tolerating a leading byte-order mark (common in source exports)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


class FixtureFetcher:
    """Offline stand-in that serves payloads from a fixture directory.

    Used by ``--fixtures-only`` mode and by unit tests. Content-type is
    derived from the file extension so the guard logic is still exercised.
    """

    def __init__(self, fixture_root: Path):
        self.fixture_root = Path(fixture_root)
        self.accepted_types = ("text/csv", "application/csv", "application/json")

    def _resolve(self, url: str) -> Path:
        name = url.rsplit("/", 1)[-1]
        if not name:
            raise IngestError(f"cannot resolve fixture for {url}")
        return self.fixture_root / name

    def fetch_bytes(self, url: str, max_bytes: Optional[int] = None) -> FetchResult:
        path = self._resolve(url)
        if not path.is_file():
            raise IngestError(f"fixture missing for {url}: {path}")
        data = path.read_bytes()
        declared = "text/csv" if path.suffix == ".csv" else "application/json"
        if not _content_type_ok(declared, self.accepted_types):
            raise ContentTypeError(f"fixture {path.name} rejected by content-type guard")
        return FetchResult(
            url=url,
            content_type=declared,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            data=data,
        )

    def download(
        self,
        url: str,
        destination: Path,
        *,
        chunk_size: int = 1 << 20,
        max_bytes: Optional[int] = None,
    ) -> FetchResult:
        result = self.fetch_bytes(url, max_bytes=max_bytes)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self._resolve(url)
        import shutil

        shutil.copyfile(source, destination)
        result.path = destination
        return result

    def fetch_text(self, url: str, encoding: str = "utf-8") -> str:
        return self._resolve(url).read_text(encoding=encoding)
