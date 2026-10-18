"""Permission-gated immutable raw downloads with bounded retry and resumability."""

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from industrypulse.contracts.observation import RawArtifact
from industrypulse.datahub.source_registry import SourceRegistry


class Fetcher:
    def __init__(
        self,
        registry: SourceRegistry,
        root: Path,
        client: httpx.Client,
        *,
        retries: int = 2,
        interval: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retries < 0 or interval < 0:
            raise ValueError("invalid retry/rate configuration")
        self.registry, self.root, self.client = registry, root, client
        self.retries, self.interval, self.sleep = retries, interval, sleep
        self._last_request: float | None = None

    def fetch(self, source_id: str, url: str, *, discovery_url: str | None = None) -> RawArtifact:
        source = self.registry.require_permission(source_id)
        host = urlsplit(source.url).hostname
        target = urlsplit(url)
        if target.scheme != "https" or target.hostname != host or target.username:
            raise PermissionError("download URL must match the approved source host")
        self.root.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()
        partial = self.root / f"{key}.part"
        resume_metadata = self.root / f"{key}.resume.json"
        for attempt in range(self.retries + 1):
            if self._last_request is not None:
                self.sleep(max(0, self.interval - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            offset = partial.stat().st_size if partial.exists() else 0
            validator = ""
            if offset and resume_metadata.exists():
                validator = str(json.loads(resume_metadata.read_text())["etag"])
            if not validator or validator.startswith("W/"):
                offset = 0
            headers = {"Range": f"bytes={offset}-", "If-Range": validator} if offset else {}
            try:
                # Redirects deliberately require separate permission validation.
                with self.client.stream(
                    "GET", url, headers=headers, follow_redirects=False
                ) as resp:
                    if resp.status_code in {429, 500, 502, 503, 504} and attempt < self.retries:
                        delay = resp.headers.get("Retry-After", "")
                        self.sleep(min(float(delay), 60) if delay.isdigit() else 2**attempt)
                        continue
                    resp.raise_for_status()
                    content_range = None
                    if resp.status_code == 206:
                        content_range = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)", resp.headers.get("Content-Range", "")
                        )
                        if (
                            not offset
                            or not content_range
                            or int(content_range.group(1)) != offset
                            or resp.headers.get("ETag") != validator
                        ):
                            raise ValueError("invalid resume Content-Range")
                    mode = "ab" if offset and resp.status_code == 206 else "wb"
                    resume_metadata.write_text(
                        json.dumps({"etag": resp.headers.get("ETag", "")}), encoding="utf-8"
                    )
                    with partial.open(mode) as out:
                        for chunk in resp.iter_bytes():
                            out.write(chunk)
                    raw = partial.read_bytes()
                    if content_range and (
                        len(raw) != int(content_range.group(3))
                        or int(content_range.group(2)) + 1 != len(raw)
                    ):
                        raise ValueError("resumed response is incomplete")
                    digest = hashlib.sha256(raw).hexdigest()
                    path = self.root / digest
                    try:
                        with path.open("xb") as out:
                            out.write(raw)
                    except FileExistsError:
                        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                            raise ValueError("immutable archive checksum mismatch") from None
                    partial.unlink()
                    resume_metadata.unlink(missing_ok=True)
                    return RawArtifact(
                        artifact_id=digest,
                        source_id=source_id,
                        uri=str(path.resolve()),
                        sha256=digest,
                        mime=resp.headers.get("Content-Type", "application/octet-stream"),
                        byte_size=len(raw),
                        retrieved_at=datetime.now(UTC),
                        source_url=url,
                        discovery_url=discovery_url,
                        http_headers=dict(resp.headers),
                    )
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
                self.sleep(2**attempt)
        raise RuntimeError("retry exhausted")
