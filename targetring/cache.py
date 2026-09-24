"""On-disk cache, keyed by source + schema version + key.

QA N4: the key must include the source schema version (and, for triage, the
prompt hash), not just gene+source. Otherwise a schema change or a prompt edit
is served from a warm cache and the fast warm path hides the bug.

Only successful, parsed payloads are cached. Failures are never cached -- a
transient 503 must not become a permanent `no_data`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import CACHE_DIR, SCHEMA_VERSIONS


class Cache:
    def __init__(self, root: Path | None = None, enabled: bool = True) -> None:
        self.root = Path(root or CACHE_DIR)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, source: str, key: str) -> Path:
        version = SCHEMA_VERSIONS.get(source)
        if version is None:
            raise KeyError(
                f"no schema version registered for cache source {source!r}; "
                f"add it to config.SCHEMA_VERSIONS so the key invalidates properly"
            )
        digest = hashlib.sha256(f"{source}|{version}|{key}".encode()).hexdigest()[:32]
        return self.root / source / f"v{version}" / f"{digest}.json"

    def get(self, source: str, key: str) -> Any | None:
        if not self.enabled:
            return None
        p = self._path(source, key)
        if not p.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            self.misses += 1
            return None
        self.hits += 1
        return payload["body"]

    def put(self, source: str, key: str, body: Any) -> None:
        if not self.enabled:
            return
        p = self._path(source, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"source": source, "key": key, "body": body}))
        tmp.replace(p)
