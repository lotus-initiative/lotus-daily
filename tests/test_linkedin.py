import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

import run_bot
from daily_lotus import linkedin, log, media


def test_is_linkedin_configured(monkeypatch) -> None:
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINKEDIN_AUTHOR_URN", raising=False)
    assert linkedin.is_linkedin_configured() is False

    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")
    assert linkedin.is_linkedin_configured() is False

    monkeypatch.setenv("LINKEDIN_AUTHOR_URN", "urn:li:organization:123")
    assert linkedin.is_linkedin_configured() is True


def test_escape_little_text_preserves_urls_and_hashtags() -> None:
    chem = "4-[(2S)-2-hydroxy-6-methylheptan-2-yl]benzoic acid"
    escaped_chem = linkedin.escape_little_text(chem)
    assert escaped_chem == r"4-\[\(2S\)-2-hydroxy-6-methylheptan-2-yl\]benzoic acid"

    verbenone = "(-)-verbenone"
    assert linkedin.escape_little_text(verbenone) == r"\(-\)-verbenone"

    mixed = "Curated at https://www.wikidata.org/wiki/Q6535827#P703 (see #1) #DailyNP #LOTUS"
    escaped_mixed = linkedin.escape_little_text(mixed)
    assert "https://www.wikidata.org/wiki/Q6535827#P703" in escaped_mixed
    assert r"\(see \#1\)" in escaped_mixed
    assert "#DailyNP #LOTUS" in escaped_mixed


def test_compose_linkedin_message_origin_link_and_length() -> None:
    with_link = linkedin.compose_linkedin_message(
        compound="(-)-verbenone",
        compound_qid="Q6535827",
        taxon="Thymus camphoratus",
        taxon_qid="Q145377",
        reference="Paper (2024)",
        reference_qid="Q58423750",
        taxon_emoji="🌿",
        kingdom_label="plant",
        mastodon_url="https://mastodon.social/@daily_lotus/12345",
    )
    assert r"\(-\)-verbenone" in with_link
    assert r"Paper \(2024\)" in with_link
    assert "https://mastodon.social/@daily_lotus/12345" in with_link
    assert "#DailyNP #OpenScience #LOTUS #Wikidata #LinkedOpenData" in with_link

    without_link = linkedin.compose_linkedin_message(
        compound="Verbenone",
        compound_qid="Q6535827",
        taxon="Thymus",
        taxon_qid="Q145377",
        reference="A" * 4000,
        reference_qid="Q58423750",
        taxon_emoji="🌿",
        kingdom_label="plant",
        mastodon_url=None,
    )
    assert "Read more & follow curation updates on Mastodon:" not in without_link
    assert len(without_link) <= linkedin.MAX_COMMENTARY_LENGTH

    long_with_link = linkedin.compose_linkedin_message(
        compound="Verbenone",
        compound_qid="Q6535827",
        taxon="Thymus",
        taxon_qid="Q145377",
        reference="A" * 4000,
        reference_qid="Q58423750",
        taxon_emoji="🌿",
        kingdom_label="plant",
        mastodon_url="https://mastodon.social/@daily_lotus/12345",
    )
    assert "https://mastodon.social/@daily_lotus/12345" in long_with_link
    assert len(long_with_link) <= linkedin.MAX_COMMENTARY_LENGTH


def test_upload_image_uses_rest_endpoint_and_put(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    class FakeResponse:
        is_redirect = False

        def __init__(self, payload: dict[str, Any] | None = None) -> None:
            self._payload = payload or {}

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return self._payload

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: int) -> FakeResponse:
        calls.append(("POST", url, headers))
        assert json == {"initializeUploadRequest": {"owner": "urn:li:organization:42"}}
        return FakeResponse({"value": {"uploadUrl": "https://upload.linkedin.com/abc", "image": "urn:li:image:99"}})

    def fake_put(
        url: str,
        *,
        headers: dict[str, str],
        data: bytes,
        timeout: int,
        allow_redirects: bool,
    ) -> FakeResponse:
        calls.append(("PUT", url, headers))
        assert data == b"PNGDATA"
        assert headers["Content-Type"] == "image/png"
        assert allow_redirects is False
        return FakeResponse()

    monkeypatch.setattr(linkedin.requests, "post", fake_post)
    monkeypatch.setattr(linkedin.requests, "put", fake_put)

    urn = linkedin.upload_image(b"PNGDATA", "image/png", "urn:li:organization:42", "tok-1", "202601")
    assert urn == "urn:li:image:99"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "https://api.linkedin.com/rest/images?action=initializeUpload"
    assert calls[0][2]["Linkedin-Version"] == "202601"
    assert calls[1][0] == "PUT"
    assert calls[1][1] == "https://upload.linkedin.com/abc"


def test_post_to_linkedin_zero_one_two_images_and_alt_cap(monkeypatch) -> None:
    posted_payloads: list[dict[str, Any]] = []
    urn_counter = 0

    class FakePostResponse:
        def __init__(self) -> None:
            self.headers = {"x-restli-id": "urn:li:share:777"}

        def raise_for_status(self) -> None:
            pass

    def fake_upload(img_bytes: bytes, mime_type: str, author_urn: str, token: str, version: str) -> str:
        nonlocal urn_counter
        urn_counter += 1
        return f"urn:li:image:{urn_counter}"

    def fake_post(url: str, *, headers: dict[str, str], data: bytes, timeout: int) -> FakePostResponse:
        assert url == "https://api.linkedin.com/rest/posts"
        posted_payloads.append(json.loads(data))
        return FakePostResponse()

    monkeypatch.setattr(linkedin, "upload_image", fake_upload)
    monkeypatch.setattr(linkedin.requests, "post", fake_post)
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("LINKEDIN_AUTHOR_URN", "urn:li:organization:42")

    res0 = linkedin.post_to_linkedin("text only", [])
    assert res0 == {"post_id": "urn:li:share:777"}
    assert "content" not in posted_payloads[0]

    res1 = linkedin.post_to_linkedin("one img", [(b"1", "image/png", "X" * 500)])
    assert res1 == {"post_id": "urn:li:share:777"}
    assert posted_payloads[1]["content"] == {"media": {"id": "urn:li:image:1", "altText": "X" * 400}}

    res2 = linkedin.post_to_linkedin(
        "two imgs",
        [(b"1", "image/png", "mol alt"), (b"2", "image/jpeg", "taxon alt")],
    )
    assert res2 == {"post_id": "urn:li:share:777"}
    assert posted_payloads[2]["content"] == {
        "multiImage": {
            "images": [
                {"id": "urn:li:image:2", "altText": "mol alt"},
                {"id": "urn:li:image:3", "altText": "taxon alt"},
            ],
        },
    }


def test_post_occurrence_to_linkedin_converts_svg_to_png(monkeypatch) -> None:
    captured_items: list[tuple[bytes, str, str]] = []

    class FakeGetResponse:
        def __init__(self, content: bytes, content_type: str) -> None:
            self.content = content
            self.headers = {"Content-Type": content_type}

        def raise_for_status(self) -> None:
            pass

    def fake_get(url: str, *, headers: dict[str, str], allow_redirects: bool, timeout: int) -> FakeGetResponse:
        if url.endswith(".svg"):
            return FakeGetResponse(b"<svg></svg>", "image/svg+xml; charset=utf-8")
        return FakeGetResponse(b"JPGDATA", "image/jpeg")

    def fake_svg2png(*, bytestring: bytes, write_to: Any) -> None:
        write_to.write(b"\x89PNGFAKE")

    def fake_post_to_linkedin(
        text: str,
        image_items: list[tuple[bytes, str, str]] | None = None,
    ) -> dict[str, str | None]:
        assert image_items is not None
        captured_items.extend(image_items)
        return {"post_id": "urn:li:share:888"}

    monkeypatch.setattr(media.requests, "get", fake_get)
    monkeypatch.setitem(sys.modules, "cairosvg", SimpleNamespace(svg2png=fake_svg2png))
    monkeypatch.setattr(linkedin, "post_to_linkedin", fake_post_to_linkedin)

    res = linkedin.post_occurrence_to_linkedin(
        "hello",
        image_url="https://example.org/mol.svg",
        taxon_image_url="https://example.org/taxon.jpg",
        image_alt_text="mol alt",
        taxon_image_alt_text="taxon alt",
    )
    assert res == {"post_id": "urn:li:share:888"}
    assert captured_items == [
        (b"\x89PNGFAKE", "image/png", "mol alt"),
        (b"JPGDATA", "image/jpeg", "taxon alt"),
    ]


def _setup_run_bot_mocks(monkeypatch, tmp_path: Path) -> Path:
    posted_path = tmp_path / "posted_log.json"
    extended_path = tmp_path / "posted_log_extended.json"
    monkeypatch.setattr(log, "LOG_FILE", str(posted_path))
    monkeypatch.setattr(log, "EXTENDED_LOG_FILE", str(extended_path))
    monkeypatch.setattr(run_bot, "get_candidate_qids", lambda **kwargs: ["Q6535827"])
    monkeypatch.setattr(
        run_bot,
        "get_molecule_details",
        lambda compound, **kwargs: {
            "compound": "(-)-verbenone",
            "compound_qid": "Q6535827",
            "taxon": "Thymus camphoratus",
            "taxon_qid": "Q145377",
            "reference": "Example Paper",
            "reference_qid": "Q58423750",
            "smiles": "CC",
            "image_url": "https://example.org/mol.png",
            "taxon_image_url": "https://example.org/taxon.jpg",
            "taxon_emoji": "🌿",
            "kingdom_label": "plant",
        },
    )
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("LINKEDIN_AUTHOR_URN", "urn:li:organization:42")
    return extended_path


def test_run_bot_linkedin_dry_run_no_linkedin_and_error_isolation(monkeypatch, tmp_path: Path) -> None:
    extended_path = _setup_run_bot_mocks(monkeypatch, tmp_path)
    mastodon_calls: list[str] = []
    linkedin_calls: list[str] = []

    monkeypatch.setattr(
        run_bot,
        "post_to_mastodon",
        lambda message, **kwargs: (
            mastodon_calls.append(message)
            or {"id": "101", "url": "https://mastodon.social/@bot/101", "visibility": "public"}
        ),
    )
    monkeypatch.setattr(
        run_bot,
        "post_occurrence_to_linkedin",
        lambda text, **kwargs: linkedin_calls.append(text) or {"post_id": "urn:li:share:202"},
    )

    assert run_bot.run(dry_run=True, include_counter=False)
    assert mastodon_calls == []
    assert linkedin_calls == []

    assert run_bot.run(no_linkedin=True, include_counter=False)
    assert len(mastodon_calls) == 1
    assert linkedin_calls == []
    assert json.loads(extended_path.read_text(encoding="utf-8"))[0]["toot_id"] == "101"

    extended_path.unlink()
    mastodon_calls.clear()

    def failing_linkedin(text: str, **kwargs: object) -> None:
        linkedin_calls.append(text)
        raise requests.HTTPError("linkedin500")

    monkeypatch.setattr(run_bot, "post_occurrence_to_linkedin", failing_linkedin)
    assert run_bot.run(include_counter=False)
    assert len(mastodon_calls) == 1
    assert len(linkedin_calls) == 1
    linkedin_calls.clear()
    saved = json.loads(extended_path.read_text(encoding="utf-8"))
    assert saved[0]["toot_id"] == "101"
    assert "linkedin_post_id" not in saved[0]

    extended_path.unlink()

    def failing_mastodon(message: str, **kwargs: object) -> None:
        raise run_bot.StatusUnconfirmedError

    monkeypatch.setattr(run_bot, "post_to_mastodon", failing_mastodon)
    monkeypatch.setattr(
        run_bot,
        "post_occurrence_to_linkedin",
        lambda text, **kwargs: linkedin_calls.append(text) or {"post_id": "urn:li:share:303"},
    )
    assert not run_bot.run(include_counter=False)
    assert linkedin_calls == []
    assert not extended_path.exists()


def test_daily_run_cross_posts_only_after_mastodon_is_recorded(monkeypatch, tmp_path: Path) -> None:
    _setup_run_bot_mocks(monkeypatch, tmp_path)
    origin = "https://mastodon.social/@daily_lotus/101"
    origin_response = {"id": "101", "visibility": "public"}
    monkeypatch.setattr(run_bot, "post_to_mastodon", lambda message, **kwargs: origin_response)
    crossposts: list[str] = []

    def crosspost(text: str, **kwargs: object) -> dict[str, str]:
        assert log.load_extended_log()[0]["toot_id"] == "101"
        crossposts.append(text)
        return {"post_id": "urn:li:share:303"}

    monkeypatch.setattr(run_bot, "post_occurrence_to_linkedin", crosspost)
    assert run_bot.run(include_counter=False)
    assert crossposts == []

    Path(log.EXTENDED_LOG_FILE).unlink()
    origin_response["url"] = origin
    assert run_bot.run(include_counter=False)
    [copy] = crossposts
    assert origin in copy
    [record] = log.load_extended_log()
    assert record["toot_id"] == "101"
    assert record["linkedin_post_id"] == "urn:li:share:303"
