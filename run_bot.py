import argparse
import json
import logging
import secrets

from daily_lotus.formatter import MessageTooLongError, compose_message
from daily_lotus.linkedin import (
    compose_linkedin_message,
    is_linkedin_configured,
    post_occurrence_to_linkedin,
)
from daily_lotus.log import record_post_extended, was_posted
from daily_lotus.mastodon_client import post_to_mastodon
from daily_lotus.wikidata_query import get_candidate_qids, get_molecule_details

logger = logging.getLogger(__name__)


def _publish_to_mastodon(
    message: str,
    details: dict[str, str],
    image_alt_text: str,
    taxon_image_alt_text: str,
) -> tuple[bool, str | None, str | None]:
    try:
        status = post_to_mastodon(
            message,
            image_url=details.get("image_url"),
            taxon_image_url=details.get("taxon_image_url"),
            image_alt_text=image_alt_text,
            taxon_image_alt_text=taxon_image_alt_text,
        )
    except Exception:
        logger.exception("❌ Mastodon publishing failed.")
        return False, None, None

    toot_id = str(status["id"]) if isinstance(status, dict) and status.get("id") else None
    mastodon_url = str(status["url"]) if isinstance(status, dict) and status.get("url") else None
    return True, toot_id, mastodon_url


def _publish_to_linkedin(
    details: dict[str, str],
    image_alt_text: str,
    taxon_image_alt_text: str,
    *,
    mastodon_url: str | None,
) -> tuple[bool, str | None]:
    linkedin_text = compose_linkedin_message(
        compound=details["compound"],
        compound_qid=details["compound_qid"],
        taxon=details["taxon"],
        taxon_qid=details["taxon_qid"],
        reference=details["reference"],
        reference_qid=details["reference_qid"],
        taxon_emoji=details["taxon_emoji"],
        kingdom_label=details["kingdom_label"],
        mastodon_url=mastodon_url,
    )
    try:
        result = post_occurrence_to_linkedin(
            linkedin_text,
            image_url=details.get("image_url"),
            taxon_image_url=details.get("taxon_image_url"),
            image_alt_text=image_alt_text,
            taxon_image_alt_text=taxon_image_alt_text,
        )
    except Exception:
        logger.exception("❌ LinkedIn publishing failed.")
        return False, None

    return True, result.get("post_id")


def _print_dry_run_preview(
    message: str,
    details: dict[str, str],
    image_alt_text: str,
    taxon_image_alt_text: str,
    *,
    no_linkedin: bool,
) -> None:
    print("🧪 Dry run mode — not posting to Mastodon or LinkedIn.")
    print("------ Message ------")
    print(message)
    print("🖼 Molecule image URL:", details.get("image_url"))
    print("🖼 Taxon image URL:", details.get("taxon_image_url"))
    print("🖼 Molecule Alt-Text:", image_alt_text)
    print("🖼 Taxon Alt-Text:", taxon_image_alt_text)
    if not no_linkedin and is_linkedin_configured():
        linkedin_preview = compose_linkedin_message(
            compound=details["compound"],
            compound_qid=details["compound_qid"],
            taxon=details["taxon"],
            taxon_qid=details["taxon_qid"],
            reference=details["reference"],
            reference_qid=details["reference_qid"],
            taxon_emoji=details["taxon_emoji"],
            kingdom_label=details["kingdom_label"],
        )
        print("------ LinkedIn Preview ------")
        print(linkedin_preview)


def run(dry_run: bool = False, use_cache: bool = False, *, no_linkedin: bool = False) -> None:
    if use_cache:
        print("📦 Loading candidate compound QIDs from cache (candidates.json)...")
        with open("candidates.json") as f:
            qids = json.load(f)
    else:
        print("📡 Fetching candidate compound QIDs from Wikidata...")
        qids = get_candidate_qids()

    secrets.SystemRandom().shuffle(qids)

    for qid in qids:
        print(f"🔍 Trying compound {qid}...")
        details = get_molecule_details(qid)

        if not details:
            continue

        compound_qid = details["compound_qid"]
        taxon_qid = details["taxon_qid"]

        if was_posted(compound_qid, taxon_qid):
            print(f"⏩ Already posted {compound_qid} + {taxon_qid}, skipping.")
            continue

        try:
            message = compose_message(
                compound=details["compound"],
                compound_qid=compound_qid,
                taxon=details["taxon"],
                taxon_qid=taxon_qid,
                reference=details["reference"],
                reference_qid=details["reference_qid"],
                taxon_emoji=details["taxon_emoji"],
                kingdom_label=details["kingdom_label"],
            )
        except MessageTooLongError as e:
            print(str(e))
            print("⏭️ Skipping this compound-taxon pair due to length constraints.")
            continue

        image_alt_text = f"Chemical structure of {details['compound']} displaying atoms and bonds."
        taxon_image_alt_text = f"Image of {details['taxon']}, the taxon in which the compound is found."

        if dry_run:
            _print_dry_run_preview(
                message,
                details,
                image_alt_text,
                taxon_image_alt_text,
                no_linkedin=no_linkedin,
            )
            break

        print("🟢 Posting:")
        print(message)
        mastodon_ok, toot_id, mastodon_url = _publish_to_mastodon(
            message,
            details,
            image_alt_text,
            taxon_image_alt_text,
        )

        linkedin_ok = False
        linkedin_post_id: str | None = None
        if not no_linkedin and is_linkedin_configured():
            linkedin_ok, linkedin_post_id = _publish_to_linkedin(
                details,
                image_alt_text,
                taxon_image_alt_text,
                mastodon_url=mastodon_url,
            )

        if mastodon_ok or linkedin_ok:
            record_post_extended(
                compound_qid=compound_qid,
                taxon_qid=taxon_qid,
                reference_qid=details["reference_qid"],
                compound_label=details["compound"],
                taxon_label=details["taxon"],
                reference_label=details["reference"],
                toot_id=toot_id,
                linkedin_post_id=linkedin_post_id,
            )
            print("✅ Posted and logged.")
        else:
            print("❌ Failed to post to both Mastodon and LinkedIn.")
        break
    else:
        print("❌ No new unique compound-taxon pair found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Daily LOTUS bot.")
    parser.add_argument("--dry-run", action="store_true", help="Run the bot without posting to Mastodon or LinkedIn.")
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Load candidate QIDs from candidates.json instead of querying Wikidata.",
    )
    parser.add_argument(
        "--no-linkedin",
        action="store_true",
        help="Skip cross-posting to LinkedIn even if credentials are configured.",
    )
    args = parser.parse_args()

    run(dry_run=args.dry_run, use_cache=args.use_cache, no_linkedin=args.no_linkedin)
