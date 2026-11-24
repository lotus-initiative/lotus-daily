import json
from pathlib import Path
from typing import Any

from daily_lotus import check_edits
from daily_lotus.purge_unsigned_edits import (
    find_unsigned_edit_toots,
    is_unsigned_edit_toot,
    purge_unsigned_edit_toots,
)


def test_is_unsigned_edit_toot_classification() -> None:
    unsigned_with_hashtag = {
        "id": "1",
        "content": (
            "<p>🛠️ This occurrence was edited on Wikidata:</p><p>• reference label changed: “” → “Paper”</p>"
            '<p><a href="https://mastodon.social/tags/DailyNPedit" class="mention hashtag">#<span>DailyNPedit</span></a></p>'
        ),
    }
    unsigned_historical = {
        "id": "2",
        "content": "<p>🛠️ This occurrence was edited on Wikidata:<br/>• compound label changed: “” → “Foo”</p>",
    }
    unsigned_deleted = {
        "id": "3",
        "content": "<p>💀 This occurrence is no longer listed on Wikidata.</p>",
    }
    signed_edit = {
        "id": "4",
        "content": (
            "<p>🛠️ This occurrence was edited on Wikidata:<br/>• compound label changed: “A” → “B”</p>"
            "<p>🧑‍🔧 Edited by: Curator</p><p>#DailyNPedit</p>"
        ),
    }
    daily_occurrence = {
        "id": "5",
        "content": "<p>📣 Natural Product Occurrence of the Day</p><p>🧪 Verbenone ...</p>",
    }
    boost = {
        "id": "6",
        "content": "<p>🛠️ This occurrence was edited on Wikidata:</p>",
        "reblog": {"id": "999"},
    }

    assert is_unsigned_edit_toot(unsigned_with_hashtag) is True
    assert is_unsigned_edit_toot(unsigned_historical) is True
    assert is_unsigned_edit_toot(unsigned_deleted) is True
    assert is_unsigned_edit_toot(signed_edit) is False
    assert is_unsigned_edit_toot(daily_occurrence) is False
    assert is_unsigned_edit_toot(boost) is False


def test_find_and_purge_unsigned_edit_toots_dry_run_and_execute() -> None:
    class FakeMastodon:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.pages: list[list[dict[str, Any]]] = [
                [
                    {
                        "id": "10",
                        "created_at": "2025-06-01T00:00:00Z",
                        "content": "<p>🛠️ This occurrence was edited on Wikidata:</p>",
                    },
                    {
                        "id": "11",
                        "created_at": "2025-06-01T00:00:00Z",
                        "content": "<p>📣 Natural Product Occurrence of the Day</p>",
                    },
                ],
                [
                    {"id": "12", "created_at": "2025-06-02T00:00:00Z", "content": "<p>#DailyNPedit</p>"},
                    {
                        "id": "13",
                        "created_at": "2025-06-02T00:00:00Z",
                        "content": "<p>🛠️ This occurrence was edited on Wikidata: 🧑‍🔧 Edited by: Alice #DailyNPedit</p>",
                    },
                ],
            ]

        def account_verify_credentials(self) -> dict[str, str]:
            return {"id": "bot-1"}

        def account_statuses(self, account_id: str, limit: int = 40) -> list[dict[str, Any]]:
            assert account_id == "bot-1"
            return self.pages[0]

        def fetch_next(self, page: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
            if page == self.pages[0]:
                return self.pages[1]
            return None

        def status_delete(self, status_id: str) -> None:
            self.deleted.append(status_id)

    client = FakeMastodon()
    matches = find_unsigned_edit_toots(client)
    assert [m["id"] for m in matches] == ["10", "12"]

    dry_ids = purge_unsigned_edit_toots(client, execute=False, delay=0.0)
    assert dry_ids == ["10", "12"]
    assert client.deleted == []

    deleted_ids = purge_unsigned_edit_toots(client, execute=True, delay=0.0, limit=1)
    assert deleted_ids == ["10"]
    assert client.deleted == ["10"]


def test_check_edits_heals_empty_reference_baseline_and_ignores_transient_empty(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "posted_log_extended.json"
    entries = [
        {
            "compound_qid": "Q1",
            "taxon_qid": "Q2",
            "reference_qid": "Q3",
            "compound_label": "Compound A",
            "compound_label_last_checked": "Compound A",
            "taxon_label": "Taxon A",
            "taxon_label_last_checked": "Taxon A",
            "reference_label": "Real Paper Title",
            "reference_label_last_checked": "",
            "toot_id": "111",
            "timestamp": "2025-05-01T00:00:00+00:00",
            "last_reply_timestamp": "2025-05-01T00:00:00+00:00",
            "p703_exists_last_checked": True,
            "smiles": "CCO",
            "smiles_last_checked": "CCO",
        },
    ]
    log_path.write_text(json.dumps(entries), encoding="utf-8")

    monkeypatch.setattr(check_edits, "LOG_FILE", log_path)
    monkeypatch.setattr(check_edits, "occurrence_still_exists", lambda c, t: True)
    monkeypatch.setattr(
        check_edits,
        "fetch_current_labels",
        lambda c, t, r: {
            "compound_label": "",
            "taxon_label": "Taxon A",
            "reference_label": "Real Paper Title",
        },
    )
    monkeypatch.setattr(check_edits, "fetch_current_smiles", lambda c: "CCO")
    monkeypatch.setattr(check_edits, "get_label_change_editor", lambda qid, old, since: None)
    monkeypatch.setattr(check_edits, "get_reference_label_change_editor", lambda qid, old, since: None)

    posts: list[str] = []
    monkeypatch.setattr(check_edits, "post_to_mastodon", lambda msg, **kwargs: posts.append(msg))

    check_edits.check_edits(dry_run=False)
    assert posts == []
    saved = json.loads(log_path.read_text(encoding="utf-8"))
    assert saved[0]["reference_label_last_checked"] == "Real Paper Title"
    assert saved[0]["compound_label_last_checked"] == "Compound A"
