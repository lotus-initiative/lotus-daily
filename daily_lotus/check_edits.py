import argparse
import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import cast

from daily_lotus.log import PostRecord, load_extended_log, merge_extended_log
from daily_lotus.logging_config import add_logging_args, configure_logging_from_args
from daily_lotus.mastodon_client import post_to_mastodon
from daily_lotus.wikidata_query import (
    build_depict_url,
    fetch_current_labels,
    fetch_current_smiles,
    find_p703_removal_editor,
    get_label_change_editor,
    get_reference_label_change_editor,
    get_smiles_change_editor,
    occurrence_still_exists,
)

logger = logging.getLogger("daily_lotus.check_edits")

LOG_FILE = Path("posted_log_extended.json")
DEBUG_LOG_FILE = Path("posted_log_extended.dryrun.json")
MAX_TOOT_LENGTH = 500


def _format_change_line(field: str, old: str, new: str, *, compact_smiles: bool = False) -> str:
    if field == "smiles":
        if compact_smiles:
            return "• SMILES structure updated (see attached depiction)"
        return f"• SMILES changed: “{old}” → “{new}”"
    label = {"compound": "compound", "taxon": "taxon", "reference": "reference"}[field]
    return f"• {label} label changed: “{old}” → “{new}”"


def _build_summary_text(
    changes: list[tuple[str, str, str]],
    editors: list[str],
    deleted: bool,
    compound_label: str,
    taxon_label: str,
    compound_qid: str,
    *,
    compact_smiles: bool = False,
) -> str:
    lines = []

    if deleted:
        lines.append("💀 This occurrence is no longer listed on Wikidata.\n")
        lines.append(f"The claim that {compound_label} was found in {taxon_label} has been removed.\n")
    else:
        lines.append("🛠️ This occurrence was edited on Wikidata:")

    for field, old, new in changes:
        lines.append(_format_change_line(field, old, new, compact_smiles=compact_smiles))

    if editors:
        mentions = ", ".join(f"[{e}](https://www.wikidata.org/wiki/User:{e})" for e in sorted(set(editors)))
        lines.append(f"\n🧑‍🔧 Edited by: {mentions}")

    lines.append(f"\n✏️ [Improve it on Wikidata](https://www.wikidata.org/wiki/{compound_qid}#P703)")
    lines.append("\n#DailyNPedit")
    return "\n".join(lines)


def format_unified_summary(
    changes: list[tuple[str, str, str]],
    editors: list[str],
    deleted: bool,
    compound_label: str,
    taxon_label: str,
    compound_qid: str,
) -> str:
    """Format a Mastodon reply summarizing detected Wikidata edits.

    Parameters
    ----------
    changes : list[tuple[str, str, str]]
        Detected ``(field, old_value, new_value)`` tuples.
    editors : list[str]
        Wikidata editors responsible for the detected changes.
    deleted : bool
        Whether the ``P703`` occurrence statement was removed.
    compound_label : str
        Stored compound label.
    taxon_label : str
        Stored taxon label.
    compound_qid : str
        Compound Wikidata QID.

    Returns
    -------
    str
        Formatted status message (compact SMILES notice if longer than 500 characters).
    """
    summary = _build_summary_text(
        changes,
        editors,
        deleted,
        compound_label,
        taxon_label,
        compound_qid,
        compact_smiles=False,
    )
    if len(summary) > MAX_TOOT_LENGTH and any(field == "smiles" for field, _, _ in changes):
        return _build_summary_text(
            changes,
            editors,
            deleted,
            compound_label,
            taxon_label,
            compound_qid,
            compact_smiles=True,
        )
    return summary


def initialize_last_checked_labels(entry: PostRecord) -> None:
    """Ensure the entry has the *_label_last_checked fields initialized.

    Parameters
    ----------
    entry : PostRecord
        Entry.
    """
    if "compound_label_last_checked" not in entry:
        entry["compound_label_last_checked"] = entry.get("compound_label", "")
    if "taxon_label_last_checked" not in entry:
        entry["taxon_label_last_checked"] = entry.get("taxon_label", "")
    if "reference_label_last_checked" not in entry:
        entry["reference_label_last_checked"] = entry.get("reference_label", "")
    if "p703_exists_last_checked" not in entry:
        entry["p703_exists_last_checked"] = True


def was_occurrence_deleted(entry: PostRecord, since: datetime) -> str | None:
    """Check if the P703 (found in taxon) statement is still present.

    Parameters
    ----------
    entry : PostRecord
        Entry.
    since : datetime
        Since.

    Returns
    -------
    str | None
        P703 claim, or ``None`` if no deletion event is detected.
    """
    occurrence_exists = occurrence_still_exists(entry["compound_qid"], entry["taxon_qid"])

    # Check for P703 relationship removal
    if not occurrence_exists:
        if entry.get("p703_exists_last_checked", True):
            logger.info("❌ Occurrence was deleted from Wikidata!")
            editor = find_p703_removal_editor(entry["compound_qid"], entry["taxon_qid"], since)
            if editor:
                logger.info("👤 P703 deleted by: %s", editor)
            entry["p703_exists_last_checked"] = False  # Store the status of the relationship
            return editor
    else:
        # If the relationship is added back, update the status
        if entry.get("p703_exists_last_checked", False) is False:
            logger.info("✔️ P703 added back to the occurrence.")
            entry["p703_exists_last_checked"] = True  # Update status to True
    return None


def _set_last_checked_label(entry: PostRecord, field: str, value: str) -> None:
    if field == "compound":
        entry["compound_label_last_checked"] = value
    elif field == "taxon":
        entry["taxon_label_last_checked"] = value
    elif field == "reference":
        entry["reference_label_last_checked"] = value


def get_label_changes(entry: PostRecord, since: datetime) -> tuple[list[tuple[str, str, str]], list[str]]:
    changes: list[tuple[str, str, str]] = []
    editors: list[str] = []
    result = fetch_current_labels(entry["compound_qid"], entry["taxon_qid"], entry["reference_qid"])

    # For each label (compound, taxon, reference), compare the current value with the last checked value
    fields = [
        (
            "compound",
            entry["compound_qid"],
            entry.get("compound_label_last_checked"),
            result["compound_label"],
            get_label_change_editor,
        ),
        (
            "taxon",
            entry["taxon_qid"],
            entry.get("taxon_label_last_checked"),
            result["taxon_label"],
            get_label_change_editor,
        ),
        (
            "reference",
            entry["reference_qid"],
            entry.get("reference_label_last_checked"),
            result["reference_label"],
            get_reference_label_change_editor,
        ),
    ]
    for field, qid, raw_last_checked, raw_new_label, editor_fn in fields:
        new_label = raw_new_label if raw_new_label is not None else ""
        last_checked_label = raw_last_checked if raw_last_checked is not None else ""
        if new_label == last_checked_label:
            continue

        editor = editor_fn(qid, last_checked_label, since)
        if not new_label and last_checked_label and not editor:
            logger.info("⏭️ Ignoring transient empty %s label for %s.", field, qid)
            continue

        if not last_checked_label and new_label and not editor:
            logger.info("🩺 Healing empty %s label baseline for %s without posting.", field, qid)
            _set_last_checked_label(entry, field, new_label)
            continue

        changes.append((field, last_checked_label, new_label))
        if editor:
            editors.append(editor)
        _set_last_checked_label(entry, field, new_label)

    return changes, editors


def get_smiles_change(
    entry: PostRecord,
    since: datetime,
) -> tuple[tuple[str, str, str] | None, str | None, bool]:
    """Check whether the compound's SMILES (``P2017`` / ``P233``) changed since the last check.

    For legacy log entries without ``smiles`` or ``smiles_last_checked``, initializes
    ``smiles_last_checked`` silently without raising an edit event.

    Parameters
    ----------
    entry : PostRecord
        Extended post log entry.
    since : datetime
        Timestamp from which to inspect Wikidata revisions.

    Returns
    -------
    tuple[tuple[str, str, str] | None, str | None, bool]
        ``(change_tuple, editor_username, baseline_initialized)``.
    """
    new_smiles = fetch_current_smiles(entry["compound_qid"])
    last_checked_smiles = entry.get("smiles_last_checked")

    if last_checked_smiles is None:
        stored_smiles = entry.get("smiles")
        if stored_smiles:
            last_checked_smiles = stored_smiles
            entry["smiles_last_checked"] = stored_smiles
        else:
            if new_smiles:
                entry["smiles_last_checked"] = new_smiles
                return None, None, True
            return None, None, False

    if new_smiles and new_smiles != last_checked_smiles:
        editor = get_smiles_change_editor(entry["compound_qid"], last_checked_smiles, since)
        entry["smiles_last_checked"] = new_smiles
        return ("smiles", last_checked_smiles, new_smiles), editor, False

    return None, None, False


def _parse_since_timestamp(entry: PostRecord) -> datetime:
    since_str = cast(str, entry.get("last_reply_timestamp", entry["timestamp"]))
    parsed = datetime.fromisoformat(since_str)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _post_edit_reply(
    entry: PostRecord,
    reply: str,
    new_smiles: str | None,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        logger.info("💤 Dry run: not posting to Mastodon.")
        return

    image_url = build_depict_url(new_smiles) if new_smiles else None
    image_alt_text = (
        f"Updated chemical structure of {entry['compound_label']} displaying atoms and bonds." if image_url else None
    )
    event = json.dumps([entry["toot_id"], _parse_since_timestamp(entry).isoformat(), reply, new_smiles])
    idempotency_key = sha256(event.encode()).hexdigest()
    post_to_mastodon(
        reply,
        image_url=image_url,
        image_alt_text=image_alt_text,
        in_reply_to_id=entry["toot_id"],
        idempotency_key=idempotency_key,
    )
    entry["last_reply_timestamp"] = datetime.now(tz=timezone.utc).isoformat()


def process_entry(entry: PostRecord, dry_run: bool) -> bool:
    """Inspect a single posted occurrence for Wikidata edits and reply if editors are found.

    Parameters
    ----------
    entry : PostRecord
        Extended post log entry.
    dry_run : bool
        When ``True``, preview replies without posting to Mastodon.

    Returns
    -------
    bool
        ``True`` if the entry state was modified and should be saved, ``False`` otherwise.
    """
    logger.info("\n🔎 Checking %s + %s + %s", entry["compound_qid"], entry["taxon_qid"], entry["reference_qid"])

    if not entry.get("toot_id"):
        logger.warning("⚠️ No toot_id available, skipping.")
        return False

    initialize_last_checked_labels(entry)
    since = _parse_since_timestamp(entry)

    editors: list[str] = []
    changes: list[tuple[str, str, str]] = []

    deleted_by = was_occurrence_deleted(entry, since)
    deleted = deleted_by is not None
    if deleted_by is not None:
        editors.append(deleted_by)

    label_snapshot = (
        entry.get("compound_label_last_checked"),
        entry.get("taxon_label_last_checked"),
        entry.get("reference_label_last_checked"),
    )
    label_changes, label_editors = get_label_changes(entry, since)
    changes.extend(label_changes)
    editors.extend(label_editors)
    labels_healed = label_snapshot != (
        entry.get("compound_label_last_checked"),
        entry.get("taxon_label_last_checked"),
        entry.get("reference_label_last_checked"),
    )

    smiles_change, smiles_editor, smiles_baseline_initialized = get_smiles_change(entry, since)
    new_smiles = smiles_change[2] if smiles_change else None
    if smiles_change:
        changes.append(smiles_change)
    if smiles_editor:
        editors.append(smiles_editor)

    if not deleted and not changes:
        if smiles_baseline_initialized or labels_healed:
            logger.info("📌 Initialized or healed baseline without posting.")
            return True
        logger.info("✅ No changes found.")
        return False

    if changes:
        logger.info("✏️ Detected edits: %s", changes)
    if editors:
        logger.info("👤 Edited by: %s", ", ".join(editors))

    reply = format_unified_summary(
        changes,
        editors,
        deleted,
        compound_label=entry["compound_label"],
        taxon_label=entry["taxon_label"],
        compound_qid=entry["compound_qid"],
    )
    logger.info("📣 Replying with:\n%s", reply)

    # We only post if editors are logged
    if not editors:
        logger.warning("⚠️ No editors found, not posting to Mastodon.")
        return False

    _post_edit_reply(entry, reply, new_smiles, dry_run=dry_run)
    return True


def check_edits(dry_run: bool = False) -> None:
    """Check all entries in ``LOG_FILE`` for Wikidata edits and post signed updates."""
    logger.info("🕒 It is: %s", datetime.now(tz=timezone.utc).isoformat())
    logger.info("🔍 Checking for edits to previously posted occurrences...")
    original = load_extended_log(LOG_FILE)
    edited = original.copy()

    for index, entry in enumerate(original):
        working_copy = entry.copy()
        try:
            if process_entry(working_copy, dry_run=dry_run):
                edited[index] = working_copy
        except Exception:
            logger.exception("❌ Failed checking edits for %s", entry.get("compound_qid", "unknown"))

    if edited != original:
        if dry_run:
            logger.info("📝 Dry run mode: would update log with:\n%s", json.dumps(edited, indent=2))
        else:
            merge_extended_log(original, edited, LOG_FILE)
            logger.info("📝 Updated log with new reply timestamps.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check for Wikidata edits and reply on Mastodon.")
    parser.add_argument("--dry-run", action="store_true", help="Print reply messages without posting to Mastodon.")
    add_logging_args(parser, "check_edits.log")
    args = parser.parse_args()
    configure_logging_from_args(args, "check_edits.log")
    check_edits(dry_run=args.dry_run)
