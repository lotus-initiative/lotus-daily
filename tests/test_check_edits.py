import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daily_lotus import check_edits, wikidata_query
from daily_lotus.log import PostRecord


def test_extract_smiles_prefers_p2017_over_p233() -> None:
    both = {
        "claims": {
            "P2017": [{"mainsnak": {"datavalue": {"value": "C[C@H](O)CC"}}}],
            "P233": [{"mainsnak": {"datavalue": {"value": "CC(O)CC"}}}],
        },
    }
    canonical_only = {
        "claims": {
            "P233": [{"mainsnak": {"datavalue": {"value": "CCO"}}}],
        },
    }
    empty: dict[str, Any] = {"claims": {}}

    assert wikidata_query.extract_smiles(both) == "C[C@H](O)CC"
    assert wikidata_query.extract_smiles(canonical_only) == "CCO"
    assert wikidata_query.extract_smiles(empty) is None


def test_get_revisions_passes_rvstart_and_prepends_parentid(monkeypatch) -> None:
    captured_params: dict[str, str] = {}

    def fake_request(url: str, *, params: dict[str, str] | None = None, **kwargs: Any) -> dict[str, Any]:
        assert params is not None
        captured_params.update(params)
        return {
            "query": {
                "pages": [
                    {
                        "revisions": [
                            {"revid": 200, "parentid": 199, "user": "Curator", "timestamp": "2025-05-01T10:00:00Z"},
                        ],
                    },
                ],
            },
        }

    monkeypatch.setattr(wikidata_query, "_request_json_with_retry", fake_request)

    revs = wikidata_query.get_revisions("Q1", datetime(2025, 5, 1, 8, 0, 0, tzinfo=timezone.utc))
    assert captured_params["rvstart"] == "2025-05-01T08:00:00Z"
    assert [r["revid"] for r in revs] == [199, 200]


def test_get_smiles_change_editor_handles_p233_p2017_and_upgrade(monkeypatch) -> None:
    revisions = [
        {"revid": 10},
        {"revid": 11, "user": "ChemCurator"},
    ]
    entity_by_rev = {
        10: {"claims": {"P233": [{"mainsnak": {"datavalue": {"value": "CC(O)CC"}}}]}},
        11: {
            "claims": {
                "P233": [{"mainsnak": {"datavalue": {"value": "CC(O)CC"}}}],
                "P2017": [{"mainsnak": {"datavalue": {"value": "C[C@H](O)CC"}}}],
            },
        },
    }

    monkeypatch.setattr(wikidata_query, "get_revisions", lambda qid, since: revisions)
    monkeypatch.setattr(wikidata_query, "get_entity_data", lambda qid, revid: entity_by_rev[revid])

    editor = wikidata_query.get_smiles_change_editor("Q1", "CC(O)CC", datetime(2025, 5, 1, tzinfo=timezone.utc))
    assert editor == "ChemCurator"


def test_process_entry_detects_signed_smiles_change_and_attaches_depiction(monkeypatch) -> None:
    posted_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(check_edits, "occurrence_still_exists", lambda c, t: True)
    monkeypatch.setattr(
        check_edits,
        "fetch_current_labels",
        lambda c, t, r: {"compound_label": "Verbenone", "taxon_label": "Artemisia", "reference_label": "Paper"},
    )
    monkeypatch.setattr(check_edits, "fetch_current_smiles", lambda c: "C[C@H]1CC1")
    monkeypatch.setattr(check_edits, "get_smiles_change_editor", lambda qid, old, since: "LotusCurator")
    monkeypatch.setattr(
        check_edits,
        "post_to_mastodon",
        lambda msg, **kwargs: posted_calls.append({"msg": msg, **kwargs}) or {"id": "999"},
    )

    entry: PostRecord = {
        "compound_qid": "Q1",
        "taxon_qid": "Q2",
        "reference_qid": "Q3",
        "compound_label": "Verbenone",
        "compound_label_last_checked": "Verbenone",
        "taxon_label": "Artemisia",
        "taxon_label_last_checked": "Artemisia",
        "reference_label": "Paper",
        "reference_label_last_checked": "Paper",
        "toot_id": "111",
        "timestamp": "2025-05-01T00:00:00+00:00",
        "last_reply_timestamp": "2025-05-01T00:00:00+00:00",
        "p703_exists_last_checked": True,
        "smiles": "CC1CC1",
        "smiles_last_checked": "CC1CC1",
    }

    changed = check_edits.process_entry(entry, dry_run=False)
    assert changed is True
    assert entry["smiles_last_checked"] == "C[C@H]1CC1"
    assert len(posted_calls) == 1
    assert "• SMILES changed: “CC1CC1” → “C[C@H]1CC1”" in posted_calls[0]["msg"]
    assert "LotusCurator" in posted_calls[0]["msg"]
    assert posted_calls[0]["in_reply_to_id"] == "111"
    assert "C%5BC%40H%5D1CC1" in posted_calls[0]["image_url"]
    assert "Verbenone" in posted_calls[0]["image_alt_text"]


def test_legacy_entry_baselines_smiles_without_posting(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "posted_log_extended.json"
    legacy_entry: dict[str, Any] = {
        "compound_qid": "Q1",
        "taxon_qid": "Q2",
        "reference_qid": "Q3",
        "compound_label": "Verbenone",
        "compound_label_last_checked": "Verbenone",
        "taxon_label": "Artemisia",
        "taxon_label_last_checked": "Artemisia",
        "reference_label": "Paper",
        "reference_label_last_checked": "Paper",
        "toot_id": "111",
        "timestamp": "2025-05-01T00:00:00+00:00",
        "last_reply_timestamp": "2025-05-01T00:00:00+00:00",
        "p703_exists_last_checked": True,
    }
    log_path.write_text(json.dumps([legacy_entry]), encoding="utf-8")

    monkeypatch.setattr(check_edits, "LOG_FILE", log_path)
    monkeypatch.setattr(check_edits, "occurrence_still_exists", lambda c, t: True)
    monkeypatch.setattr(
        check_edits,
        "fetch_current_labels",
        lambda c, t, r: {"compound_label": "Verbenone", "taxon_label": "Artemisia", "reference_label": "Paper"},
    )
    monkeypatch.setattr(check_edits, "fetch_current_smiles", lambda c: "CC1=CC(=O)C2CC1C2(C)C")

    post_calls: list[str] = []
    monkeypatch.setattr(check_edits, "post_to_mastodon", lambda msg, **kwargs: post_calls.append(msg))

    check_edits.check_edits(dry_run=False)
    assert post_calls == []
    saved = json.loads(log_path.read_text(encoding="utf-8"))
    assert saved[0]["smiles_last_checked"] == "CC1=CC(=O)C2CC1C2(C)C"


def test_format_unified_summary_compacts_huge_smiles_under_500_chars() -> None:
    long_old = "C" * 300
    long_new = "N" * 300
    summary = check_edits.format_unified_summary(
        changes=[("smiles", long_old, long_new)],
        editors=["Curator"],
        deleted=False,
        compound_label="Big Molecule",
        taxon_label="Plantae",
        compound_qid="Q123",
    )
    assert len(summary) <= 500
    assert "SMILES structure updated" in summary


def test_check_edits_isolates_per_entry_failures(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "posted_log_extended.json"
    entries = [
        {
            "compound_qid": "Q1",
            "taxon_qid": "Q2",
            "reference_qid": "Q3",
            "compound_label": "Bad",
            "taxon_label": "Taxon",
            "reference_label": "Ref",
            "toot_id": "1",
            "timestamp": "2025-05-01T00:00:00+00:00",
        },
        {
            "compound_qid": "Q4",
            "taxon_qid": "Q5",
            "reference_qid": "Q6",
            "compound_label": "Good",
            "taxon_label": "Taxon",
            "reference_label": "Ref",
            "toot_id": "2",
            "timestamp": "2025-05-01T00:00:00+00:00",
        },
    ]
    log_path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(check_edits, "LOG_FILE", log_path)

    seen: list[str] = []

    def fake_process(entry: PostRecord, *, dry_run: bool) -> bool:
        seen.append(entry["compound_qid"])
        if entry["compound_qid"] == "Q1":
            raise RuntimeError("boom")
        return False

    monkeypatch.setattr(check_edits, "process_entry", fake_process)
    check_edits.check_edits(dry_run=True)
    assert seen == ["Q1", "Q4"]
