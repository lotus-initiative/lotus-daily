import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

LOG_FILE = "posted_log.json"
EXTENDED_LOG_FILE = "posted_log_extended.json"


def write_json_atomic(path: Path | str, data: Any) -> None:
    """Atomically write JSON data to ``path`` via a temporary file in the same directory.

    Parameters
    ----------
    path : Path | str
        Destination JSON file path.
    data : Any
        JSON-serializable object to persist.
    """
    target = Path(path)
    parent = target.parent if target.parent != Path() else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(data, tmp_file, indent=2)
            tmp_file.write("\n")
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def load_log() -> list[tuple[str, str]]:
    if not os.path.exists(LOG_FILE):
        return []

    with open(LOG_FILE, encoding="utf-8") as f:
        data = json.load(f)
        return [tuple(item) for item in data]  # Convert list[list[str]] → list[tuple[str, str]]


def load_extended_log(path: Path | str | None = None) -> list["PostRecord"]:
    target = Path(path) if path is not None else Path(EXTENDED_LOG_FILE)
    if not target.exists():
        return []
    with target.open(encoding="utf-8") as f:
        return cast(list[PostRecord], json.load(f))


def save_extended_log(log: list["PostRecord"], path: Path | str | None = None) -> None:
    """Persist the extended post log atomically.

    Parameters
    ----------
    log : list[PostRecord]
        Entries to save.
    path : Path | str | None
        Destination path (defaults to ``EXTENDED_LOG_FILE``).
    """
    target = path if path is not None else EXTENDED_LOG_FILE
    write_json_atomic(target, log)


def was_posted(compound_qid: str, taxon_qid: str) -> bool:
    if (compound_qid, taxon_qid) in load_log():
        return True
    return any(
        entry.get("compound_qid") == compound_qid and entry.get("taxon_qid") == taxon_qid
        for entry in load_extended_log()
    )


def record_post(compound_qid: str, taxon_qid: str) -> None:
    posted = load_log()
    posted.append((compound_qid, taxon_qid))  # Use tuple
    write_json_atomic(LOG_FILE, posted)


class PostRecord(TypedDict):
    compound_qid: str
    taxon_qid: str
    reference_qid: str
    compound_label: str
    taxon_label: str
    reference_label: str
    toot_id: str | None
    timestamp: str
    last_reply_timestamp: str | None
    compound_label_last_checked: str | None
    taxon_label_last_checked: str | None
    reference_label_last_checked: str | None
    p703_exists_last_checked: bool | None
    smiles: NotRequired[str | None]
    smiles_last_checked: NotRequired[str | None]


def record_post_extended(
    compound_qid: str,
    taxon_qid: str,
    reference_qid: str,
    compound_label: str,
    taxon_label: str,
    reference_label: str,
    toot_id: str | None,
    smiles: str | None = None,
) -> None:
    log = load_extended_log()
    now = datetime.now(timezone.utc).isoformat()
    record: PostRecord = {
        "compound_qid": compound_qid,
        "taxon_qid": taxon_qid,
        "reference_qid": reference_qid,
        "compound_label": compound_label,
        "compound_label_last_checked": compound_label,
        "taxon_label": taxon_label,
        "taxon_label_last_checked": taxon_label,
        "reference_label": reference_label,
        "reference_label_last_checked": reference_label,
        "toot_id": toot_id,
        "timestamp": now,
        "last_reply_timestamp": now,
        "p703_exists_last_checked": True,
    }
    if smiles is not None:
        record["smiles"] = smiles
        record["smiles_last_checked"] = smiles
    log.append(record)
    save_extended_log(log)
