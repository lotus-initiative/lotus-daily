from datetime import date
from pathlib import Path
from typing import Any

import run_bot
from daily_lotus import log, themes
from daily_lotus.formatter import compose_message


def test_get_theme_for_date_all_weekdays() -> None:
    expected = {
        0: "alkaloid",
        1: "insect",
        2: "big-fat-np",
        3: "freshest",
        4: None,
        5: None,
        6: None,
    }
    base_monday = date(2026, 11, 16)
    for offset, expected_slug in expected.items():
        target = date.fromordinal(base_monday.toordinal() + offset)
        resolved = themes.get_theme_for_date(target)
        actual_slug = resolved.slug if resolved is not None else None
        assert actual_slug == expected_slug


def test_get_theme_and_list_themes() -> None:
    all_themes = themes.list_themes()
    assert len(all_themes) >= 8
    assert themes.get_theme("INSECT").taxon_ancestor_qid == "Q1390"

    raised = False
    try:
        themes.get_theme("nonexistent")
    except themes.UnknownThemeError:
        raised = True
    assert raised


def test_build_theme_queries_and_details_clauses() -> None:
    alkaloid_q = themes.build_theme_candidate_query(themes.get_theme("alkaloid"))
    assert "wd:Q70702" in alkaloid_q
    assert "PREFIX wdt:" in alkaloid_q

    insect_q = themes.build_theme_candidate_query(themes.get_theme("insect"))
    assert "wd:Q1390" in insect_q

    big_fat_q = themes.build_theme_candidate_query(themes.get_theme("big-fat-np"))
    assert "wdt:P2067" in big_fat_q
    assert "800" in big_fat_q

    freshest_qlever = themes.build_theme_candidate_query(
        themes.get_theme("freshest"),
        use_scholarly_service=False,
        reference_year_cutoff=2024,
    )
    assert "2024-01-01T00:00:00Z" in freshest_qlever
    assert "SERVICE <https://query-scholarly.wikidata.org/sparql>" not in freshest_qlever

    freshest_wdqs = themes.build_theme_candidate_query(
        themes.get_theme("freshest"),
        use_scholarly_service=True,
        reference_year_cutoff=2024,
    )
    assert "SERVICE <https://query-scholarly.wikidata.org/sparql>" in freshest_wdqs
    assert "2024-01-01T00:00:00Z" in freshest_wdqs

    details_qlever = themes.build_theme_details_clause(
        themes.get_theme("freshest"),
        use_scholarly_service=False,
        reference_year_cutoff=2024,
    )
    details_wdqs = themes.build_theme_details_clause(
        themes.get_theme("freshest"),
        use_scholarly_service=True,
        reference_year_cutoff=2024,
    )
    assert "wdt:P577" in details_qlever
    assert "SERVICE <https://query-scholarly.wikidata.org/sparql>" not in details_qlever
    assert "SERVICE <https://query-scholarly.wikidata.org/sparql>" in details_wdqs


def test_compose_message_with_theme_header_and_hashtag() -> None:
    theme = themes.get_theme("insect")
    msg = compose_message(
        compound="Cantharidin",
        compound_qid="Q412321",
        taxon="Lytta vesicatoria",
        taxon_qid="Q279325",
        reference="Short Paper",
        reference_qid="Q123456",
        taxon_emoji="🐛",
        kingdom_label="animal",
        header=theme.header,
        theme_hashtag=theme.hashtag,
    )
    assert msg.startswith("📣 Tuesday's Insect Compound")
    assert "#DailyNP #OpenScience #InsectTuesday" in msg
    assert len(msg) <= 500

    # When slightly tight, secondary hashtags drop before theme_hashtag
    tight = compose_message(
        compound="Cantharidin",
        compound_qid="Q412321",
        taxon="Lytta vesicatoria",
        taxon_qid="Q279325",
        reference="A" * 55,
        reference_qid="Q123456",
        taxon_emoji="🐛",
        kingdom_label="animal",
        header=theme.header,
        theme_hashtag=theme.hashtag,
    )
    assert "#InsectTuesday" in tight
    assert "#LOTUS #Wikidata #LinkedOpenData" not in tight
    assert len(tight) <= 500


def test_run_bot_themed_and_fallback(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setattr(log, "LOG_FILE", str(tmp_path / "posted_log.json"))
    monkeypatch.setattr(log, "EXTENDED_LOG_FILE", str(tmp_path / "posted_log_extended.json"))

    cache_path = tmp_path / "candidates.json"
    cache_path.write_text('["Q_UNTEMED_CACHE"]', encoding="utf-8")
    monkeypatch.setattr(run_bot, "CANDIDATES_CACHE_FILE", cache_path)

    candidate_calls: list[str | None] = []

    def fake_get_candidates(*, theme: themes.Theme | None = None, **kwargs: Any) -> list[str]:
        candidate_calls.append(theme.slug if theme else None)
        if theme is not None:
            return ["Q_INSECT_1"]
        return ["Q_UNTEMED_LIVE"]

    def fake_details(qid: str, *, theme: themes.Theme | None = None, **kwargs: Any) -> dict[str, str]:
        return {
            "compound": f"Compound {qid}",
            "compound_qid": qid,
            "taxon": "Lytta vesicatoria",
            "taxon_qid": "Q279325",
            "reference": "Short Paper",
            "reference_qid": "Q123456",
            "smiles": "CC",
            "image_url": "https://example.org/mol.png",
            "taxon_image_url": "https://example.org/taxon.jpg",
            "taxon_emoji": "🐛",
            "kingdom_label": "animal",
        }

    monkeypatch.setattr(run_bot, "get_candidate_qids", fake_get_candidates)
    monkeypatch.setattr(run_bot, "get_molecule_details", fake_details)

    with caplog.at_level("INFO"):
        ok = run_bot.run(dry_run=True, use_cache=True, theme="insect", include_counter=False, retry_delay=0.0)
    assert ok is True
    assert candidate_calls == ["insect"]
    assert "📣 Tuesday's Insect Compound" in caplog.text
    assert "#InsectTuesday" in caplog.text

    def empty_themed(*, theme: themes.Theme | None = None, **kwargs: Any) -> list[str]:
        if theme is not None:
            return []
        return ["Q_FALLBACK"]

    monkeypatch.setattr(run_bot, "get_candidate_qids", empty_themed)
    assert (
        run_bot.run(
            dry_run=True,
            use_cache=False,
            theme="insect",
            strict_theme=True,
            include_counter=False,
            retry_delay=0.0,
        )
        is False
    )
    assert (
        run_bot.run(
            dry_run=True,
            use_cache=False,
            theme="insect",
            strict_theme=False,
            include_counter=False,
            retry_delay=0.0,
        )
        is True
    )
