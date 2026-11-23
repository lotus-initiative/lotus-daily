import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from daily_lotus import counter, log
from daily_lotus.formatter import compose_message
from daily_lotus.wikidata_query import SparqlQueryError


def test_build_occurrence_stats_arithmetic() -> None:
    stats = counter.build_occurrence_stats(522_000, 0, source="fallback")
    assert stats.remaining_occurrences == 522_000
    assert stats.remaining_days == 522_000
    assert 1428 < stats.remaining_years < 1430


def test_count_posted_occurrences_deduplicates_across_logs(monkeypatch, tmp_path: Path) -> None:
    posted_path = tmp_path / "posted_log.json"
    extended_path = tmp_path / "posted_log_extended.json"
    posted_path.write_text(json.dumps([["Q1", "Q2"], ["Q3", "Q4"]]), encoding="utf-8")
    extended_path.write_text(
        json.dumps([
            {"compound_qid": "Q1", "taxon_qid": "Q2"},
            {"compound_qid": "Q5", "taxon_qid": "Q6"},
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(log, "LOG_FILE", str(posted_path))
    monkeypatch.setattr(log, "EXTENDED_LOG_FILE", str(extended_path))

    assert counter.count_posted_occurrences() == 3


def test_get_occurrence_stats_cache_expiry_and_fallback(monkeypatch, tmp_path: Path) -> None:
    cache_path = tmp_path / "occurrence_stats.json"
    monkeypatch.setattr(counter, "count_posted_occurrences", lambda: 10)

    monkeypatch.setattr(counter, "fetch_total_occurrences", lambda **kwargs: 545_000)
    live = counter.get_occurrence_stats(cache_path=cache_path)
    assert live.total_occurrences == 545_000
    assert live.posted_count == 10
    assert live.remaining_occurrences == 544_990
    assert live.source == "wikidata"
    assert cache_path.exists()

    def should_not_query(**kwargs: Any) -> int:
        raise AssertionError

    monkeypatch.setattr(counter, "fetch_total_occurrences", should_not_query)
    cached = counter.get_occurrence_stats(cache_path=cache_path, max_age_hours=24.0)
    assert cached.source == "cache"
    assert cached.total_occurrences == 545_000

    stale_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
    cache_path.write_text(
        json.dumps({"total_occurrences": 540_000, "fetched_at": stale_ts, "source": "wikidata"}),
        encoding="utf-8",
    )

    def fail_sparql(**kwargs: Any) -> int:
        raise SparqlQueryError()

    monkeypatch.setattr(counter, "fetch_total_occurrences", fail_sparql)
    stale = counter.get_occurrence_stats(cache_path=cache_path, max_age_hours=24.0)
    assert stale.source == "stale_cache"
    assert stale.total_occurrences == 540_000

    fallback = counter.get_occurrence_stats(cache_path=tmp_path / "missing.json", max_age_hours=24.0)
    assert fallback.source == "fallback"
    assert fallback.total_occurrences == counter.FALLBACK_TOTAL_OCCURRENCES


def test_format_counter_line_and_summary() -> None:
    stats = counter.build_occurrence_stats(522_000, 99, source="wikidata")
    line = counter.format_counter_line(stats, for_next_post=True)
    assert "#100 / 522,000" in line
    assert "1,429 yrs left" in line

    summary = counter.format_counter_summary(stats)
    assert "522,000" in summary
    assert "99" in summary


def test_compose_message_includes_and_drops_counter_first() -> None:
    counter_line = "⏳ Occurrence #1 / 522,000 (~1,429 yrs left)"
    msg = compose_message(
        compound="Verbenone",
        compound_qid="Q6535827",
        taxon="Thymus",
        taxon_qid="Q145377",
        reference="Short Title",
        reference_qid="Q58423750",
        taxon_emoji="🌿",
        kingdom_label="plant",
        counter_text=counter_line,
    )
    assert counter_line in msg
    assert "#LOTUS #Wikidata #LinkedOpenData" in msg
    assert len(msg) <= 500

    tight_msg = compose_message(
        compound="Verbenone",
        compound_qid="Q6535827",
        taxon="Thymus camphoratus",
        taxon_qid="Q145377",
        reference="A" * 35,
        reference_qid="Q58423750",
        taxon_emoji="🌿",
        kingdom_label="plant",
        counter_text=counter_line,
    )
    assert counter_line not in tight_msg
    assert "#LOTUS #Wikidata #LinkedOpenData" in tight_msg
    assert len(tight_msg) <= 500
