import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

import run_bot
from daily_lotus import log, mastodon_client, wikidata_query


def test_execute_sparql_retries_and_falls_back(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeQueryResult:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def convert(self) -> dict[str, Any]:
            return self._payload

    class FakeSPARQLWrapper:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint
            self.query_text = ""

        def addCustomHttpHeader(self, name: str, value: str) -> None:
            pass

        def setTimeout(self, timeout: int) -> None:
            pass

        def setQuery(self, query: str) -> None:
            self.query_text = query

        def setReturnFormat(self, fmt: str) -> None:
            pass

        def query(self) -> FakeQueryResult:
            calls.append((self.endpoint, self.query_text))
            if self.endpoint == wikidata_query.QLEVER_ENDPOINT:
                raise TimeoutError("timeout")
            return FakeQueryResult({"results": {"bindings": [{"ok": {"value": "1"}}]}})

    monkeypatch.setattr(wikidata_query, "SPARQLWrapper", FakeSPARQLWrapper)

    result = wikidata_query.execute_sparql(
        [
            (wikidata_query.QLEVER_ENDPOINT, "SELECT ?a WHERE {}"),
            (wikidata_query.WD_ENDPOINT, "SELECT ?b WHERE {}"),
        ],
        max_retries=2,
        retry_delay=0.0,
    )

    assert result == {"results": {"bindings": [{"ok": {"value": "1"}}]}}
    assert calls == [
        (wikidata_query.QLEVER_ENDPOINT, "SELECT ?a WHERE {}"),
        (wikidata_query.QLEVER_ENDPOINT, "SELECT ?a WHERE {}"),
        (wikidata_query.WD_ENDPOINT, "SELECT ?b WHERE {}"),
    ]


def test_execute_sparql_raises_when_all_fail(monkeypatch) -> None:
    class FailingSPARQLWrapper:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

        def addCustomHttpHeader(self, name: str, value: str) -> None:
            pass

        def setTimeout(self, timeout: int) -> None:
            pass

        def setQuery(self, query: str) -> None:
            pass

        def setReturnFormat(self, fmt: str) -> None:
            pass

        def query(self) -> Any:
            raise TimeoutError("timeout")

    monkeypatch.setattr(wikidata_query, "SPARQLWrapper", FailingSPARQLWrapper)

    raised = False
    try:
        wikidata_query.execute_sparql(
            [(wikidata_query.WD_ENDPOINT, "SELECT * WHERE {}")],
            max_retries=2,
            retry_delay=0.0,
        )
    except wikidata_query.SparqlQueryError:
        raised = True
    assert raised


def test_was_posted_checks_both_logs_and_atomic_write(monkeypatch, tmp_path: Path) -> None:
    posted_path = tmp_path / "posted_log.json"
    extended_path = tmp_path / "posted_log_extended.json"
    monkeypatch.setattr(log, "LOG_FILE", str(posted_path))
    monkeypatch.setattr(log, "EXTENDED_LOG_FILE", str(extended_path))

    assert not log.was_posted("Q1", "Q2")

    log.record_post_extended(
        compound_qid="Q1",
        taxon_qid="Q2",
        reference_qid="Q3",
        compound_label="Compound",
        taxon_label="Taxon",
        reference_label="Reference",
        toot_id="12345",
    )

    assert log.was_posted("Q1", "Q2")
    assert not log.was_posted("Q1", "Q99")
    saved = json.loads(extended_path.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["toot_id"] == "12345"


def test_fetch_image_retries_and_converts_svg(monkeypatch) -> None:
    attempts = 0

    class FakeResponse:
        def __init__(self, content: bytes, content_type: str) -> None:
            self.content = content
            self.headers = {"Content-Type": content_type}
            self.status_code = 200

        def raise_for_status(self) -> None:
            pass

    def fake_get(url: str, *, headers: dict[str, str], allow_redirects: bool, timeout: int) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise requests.ConnectionError("transient")
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
        return FakeResponse(svg, "image/svg+xml; charset=utf-8")

    def fake_svg2png(*, bytestring: bytes, write_to: Any) -> None:
        assert b"<svg" in bytestring
        write_to.write(b"\x89PNG\r\n\x1a\nFAKEPNG")

    monkeypatch.setattr(mastodon_client.requests, "get", fake_get)
    monkeypatch.setitem(sys.modules, "cairosvg", SimpleNamespace(svg2png=fake_svg2png))

    img_bytes, mime = mastodon_client.fetch_image(
        "https://example.org/molecule.svg",
        max_retries=2,
        retry_delay=0.0,
    )
    assert attempts == 2
    assert mime == "image/png"
    assert img_bytes.startswith(b"\x89PNG")


def _sample_details(qid: str) -> dict[str, str]:
    return {
        "compound": f"Compound {qid}",
        "compound_qid": qid,
        "taxon": "Artemisia annua",
        "taxon_qid": "Q1308044",
        "reference": "Example Paper",
        "reference_qid": "Q999",
        "smiles": "CC",
        "image_url": "https://example.org/mol.png",
        "taxon_image_url": "https://example.org/taxon.jpg",
        "taxon_emoji": "🌿",
        "kingdom_label": "plant",
    }


def test_run_bot_skips_failed_candidate_and_falls_back_from_corrupt_cache(monkeypatch, tmp_path: Path) -> None:
    cache_path = tmp_path / "candidates.json"
    cache_path.write_text("{corrupt json", encoding="utf-8")
    monkeypatch.setattr(run_bot, "CANDIDATES_CACHE_FILE", cache_path)
    monkeypatch.setattr(log, "LOG_FILE", str(tmp_path / "posted_log.json"))
    monkeypatch.setattr(log, "EXTENDED_LOG_FILE", str(tmp_path / "posted_log_extended.json"))

    monkeypatch.setattr(run_bot, "get_candidate_qids", lambda **kwargs: ["Q100", "Q200"])

    class DeterministicRandom:
        def shuffle(self, seq: list[str]) -> None:
            pass

    monkeypatch.setattr(run_bot.secrets, "SystemRandom", DeterministicRandom)

    def fake_details(qid: str, **kwargs: Any) -> dict[str, str]:
        if qid == "Q100":
            raise wikidata_query.SparqlQueryError()
        return _sample_details(qid)

    posted_messages: list[str] = []

    def fake_post(message: str, **kwargs: Any) -> dict[str, Any]:
        posted_messages.append(message)
        return {"id": "999"}

    monkeypatch.setattr(run_bot, "get_molecule_details", fake_details)
    monkeypatch.setattr(run_bot, "post_to_mastodon", fake_post)

    ok = run_bot.run(dry_run=False, use_cache=True, max_retries=1, retry_delay=0.0, max_candidate_attempts=3)
    assert ok is True
    assert len(posted_messages) == 1
    assert "Q200" in posted_messages[0]
    assert log.was_posted("Q200", "Q1308044")


def test_run_bot_returns_false_when_max_attempts_exhausted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(log, "LOG_FILE", str(tmp_path / "posted_log.json"))
    monkeypatch.setattr(log, "EXTENDED_LOG_FILE", str(tmp_path / "posted_log_extended.json"))
    monkeypatch.setattr(run_bot, "get_candidate_qids", lambda **kwargs: ["Q1", "Q2", "Q3"])

    def always_fail(qid: str, **kwargs: Any) -> dict[str, str]:
        raise wikidata_query.SparqlQueryError()

    monkeypatch.setattr(run_bot, "get_molecule_details", always_fail)

    ok = run_bot.run(dry_run=True, use_cache=False, max_retries=1, retry_delay=0.0, max_candidate_attempts=2)
    assert ok is False


def test_post_to_mastodon_does_not_post_status_when_image_fetch_fails(monkeypatch) -> None:
    status_calls: list[str] = []

    class FakeClient:
        def media_post(self, *args: Any, **kwargs: Any) -> str:
            return "media-1"

        def status_post(self, message: str, **kwargs: Any) -> dict[str, Any]:
            status_calls.append(message)
            return {"id": "1"}

    def fail_fetch(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr(mastodon_client, "get_client", FakeClient)
    monkeypatch.setattr(mastodon_client, "fetch_image", fail_fetch)

    raised = False
    try:
        mastodon_client.post_to_mastodon(
            "hello",
            image_url="https://example.org/mol.png",
            max_retries=1,
            retry_delay=0.0,
        )
    except requests.ConnectionError:
        raised = True
    assert raised
    assert status_calls == []


def test_run_bot_skips_when_post_to_mastodon_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(log, "LOG_FILE", str(tmp_path / "posted_log.json"))
    monkeypatch.setattr(log, "EXTENDED_LOG_FILE", str(tmp_path / "posted_log_extended.json"))
    monkeypatch.setattr(run_bot, "get_candidate_qids", lambda **kwargs: ["Q100", "Q200"])

    class DeterministicRandom:
        def shuffle(self, seq: list[str]) -> None:
            pass

    monkeypatch.setattr(run_bot.secrets, "SystemRandom", DeterministicRandom)
    monkeypatch.setattr(run_bot, "get_molecule_details", lambda qid, **kwargs: _sample_details(qid))

    attempts: list[str] = []

    def flaky_post(message: str, **kwargs: Any) -> dict[str, Any]:
        attempts.append(message)
        if "Q100" in message:
            raise requests.ConnectionError("unreachable")
        return {"id": "777"}

    monkeypatch.setattr(run_bot, "post_to_mastodon", flaky_post)

    ok = run_bot.run(dry_run=False, use_cache=False, max_retries=1, retry_delay=0.0, max_candidate_attempts=3)
    assert ok is True
    assert len(attempts) == 2
    assert log.was_posted("Q200", "Q1308044")
    assert not log.was_posted("Q100", "Q1308044")
