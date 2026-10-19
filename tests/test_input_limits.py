"""`/api/analyse` refuses an oversize table before parsing it, and a table the
CSV reader cannot read is a 422, never a 500 with a traceback.

Found 2026-10-18: a single field over `csv.field_size_limit()` (131,072 bytes)
raised `_csv.Error` out of `de_input.parse_de_table` and reached the route's
catch-all as HTTP 500. Both tests run with no network: the size check happens
before `run_analysis`, and the parse happens inside it before any client is
opened.
"""

from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

from targetring import config
from targetring.app import app

HEAD = "gene,logFC,padj\n"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(config, "DEMO_MODE", False)
    return TestClient(app)


class TestTheUploadCap:
    def test_the_cap_is_documented_and_generous(self) -> None:
        # 10 MB: ~60x a 5,000-row table (PLAN §4's upper case).
        assert config.MAX_UPLOAD_BYTES == 10 * 1024 * 1024

    def test_a_pasted_table_over_the_cap_is_a_413_with_a_sentence(
            self, client, monkeypatch) -> None:
        monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 200)
        table = HEAD + "".join(f"G{i},1.0,0.01\n" for i in range(50))
        assert len(table.encode()) > 200
        r = client.post("/api/analyse", data={"table": table})
        assert r.status_code == 413, r.text[:300]
        body = r.json()
        assert body["error"] == "input_too_large"
        assert "200 bytes" in body["message"]
        assert "traceback" not in body

    def test_an_uploaded_file_over_the_cap_is_the_same_413(
            self, client, monkeypatch) -> None:
        monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 200)
        table = HEAD + "".join(f"G{i},1.0,0.01\n" for i in range(50))
        r = client.post("/api/analyse",
                        files={"file": ("de.csv", table.encode(), "text/csv")})
        assert r.status_code == 413 and r.json()["error"] == "input_too_large"

    def test_a_table_under_the_cap_is_not_refused_by_the_cap(
            self, client, monkeypatch) -> None:
        """Past the cap check the route calls `run_analysis`, which is live
        code; it is stubbed to prove only that the cap let the table through."""
        from targetring import app as app_mod
        from targetring.de_input import InputRejected

        async def stub(**kw):
            raise InputRejected("reached the parser")
        monkeypatch.setattr(app_mod, "run_analysis", stub)
        r = client.post("/api/analyse", data={"table": HEAD + "BRAF,2.1,0.01\n"})
        assert r.status_code == 422 and r.json()["message"] == "reached the parser"


class TestACsvErrorIsARefusal:
    def test_an_oversize_field_is_a_422_not_a_500(self, client) -> None:
        blob = "x" * (csv.field_size_limit() + 10)
        table = HEAD + f'BRAF,2.1,0.01\n"{blob}",1.0,0.5\n'
        assert len(table.encode()) < config.MAX_UPLOAD_BYTES
        r = client.post("/api/analyse", data={"table": table})
        assert r.status_code == 422, r.text[:300]
        body = r.json()
        assert body["error"] == "input_rejected"
        assert "could not be read as CSV" in body["message"]
        assert "traceback" not in body
