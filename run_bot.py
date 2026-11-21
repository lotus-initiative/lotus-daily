import argparse
import json
import logging
import secrets
import sys
import time
from pathlib import Path

from daily_lotus.counter import format_counter_line, format_counter_summary, get_occurrence_stats
from daily_lotus.formatter import MessageTooLongError, compose_message
from daily_lotus.log import record_post_extended, was_posted
from daily_lotus.logging_config import add_logging_args, configure_logging_from_args
from daily_lotus.mastodon_client import post_to_mastodon
from daily_lotus.themes import Theme, UnknownThemeError, get_theme, get_theme_for_date, list_themes
from daily_lotus.wikidata_query import SparqlQueryError, get_candidate_qids, get_molecule_details

logger = logging.getLogger("daily_lotus.run_bot")

CANDIDATES_CACHE_FILE = Path("candidates.json")


def load_candidates(
    *,
    use_cache: bool,
    max_retries: int,
    retry_delay: float,
    theme: Theme | None = None,
    cache_path: Path = CANDIDATES_CACHE_FILE,
) -> list[str]:
    """Load candidate QIDs from cache or Wikidata, falling back to live SPARQL on cache errors.

    When ``theme`` is provided, the unthemed ``candidates.json`` cache is bypassed.

    Parameters
    ----------
    use_cache : bool
        Whether to attempt loading from ``cache_path`` first (ignored when ``theme`` is set).
    max_retries : int
        Maximum retry attempts per SPARQL endpoint.
    retry_delay : float
        Base delay in seconds between retries.
    theme : Theme | None
        Optional active theme filtering candidate compounds.
    cache_path : Path
        Path to the candidate QID cache file.

    Returns
    -------
    list[str]
        Candidate compound QIDs.
    """
    if use_cache and theme is None:
        logger.info("📦 Loading candidate compound QIDs from cache (%s)...", cache_path)
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("⚠️ Failed to load cache (%s): %s. Falling back to live query.", cache_path, exc)
        else:
            if isinstance(data, list) and data:
                return [str(item) for item in data]
            logger.warning("⚠️ Candidate cache %s is empty or invalid. Falling back to live query.", cache_path)

    if theme is not None:
        logger.info("🎨 Fetching candidate compound QIDs for theme '%s'...", theme.slug)
    else:
        logger.info("📡 Fetching candidate compound QIDs from Wikidata...")
    return get_candidate_qids(theme=theme, max_retries=max_retries, retry_delay=retry_delay)


def _compose_candidate_message(
    details: dict[str, str],
    *,
    counter_text: str | None = None,
    theme: Theme | None = None,
) -> str | None:
    try:
        return compose_message(
            compound=details["compound"],
            compound_qid=details["compound_qid"],
            taxon=details["taxon"],
            taxon_qid=details["taxon_qid"],
            reference=details["reference"],
            reference_qid=details["reference_qid"],
            taxon_emoji=details["taxon_emoji"],
            kingdom_label=details["kingdom_label"],
            counter_text=counter_text,
            header=theme.header if theme else None,
            theme_hashtag=theme.hashtag if theme else None,
        )
    except MessageTooLongError as exc:
        logger.warning("%s", exc)
        logger.info("⏭️ Skipping this compound-taxon pair due to length constraints.")
        return None


def _log_dry_run_preview(
    message: str,
    details: dict[str, str],
    image_alt_text: str,
    taxon_image_alt_text: str,
) -> None:
    logger.info("🧪 Dry run mode — not posting to Mastodon.")
    logger.info("------ Message ------\n%s", message)
    logger.info("🖼 Molecule image URL: %s", details.get("image_url"))
    logger.info("🖼 Taxon image URL: %s", details.get("taxon_image_url"))
    logger.info("🖼 Molecule Alt-Text: %s", image_alt_text)
    logger.info("🖼 Taxon Alt-Text: %s", taxon_image_alt_text)


def _publish_candidate(
    details: dict[str, str],
    message: str,
    *,
    dry_run: bool,
    max_retries: int,
    retry_delay: float,
) -> None:
    image_alt_text = f"Chemical structure of {details['compound']} displaying atoms and bonds."
    taxon_image_alt_text = f"Image of {details['taxon']}, the taxon in which the compound is found."

    if dry_run:
        _log_dry_run_preview(message, details, image_alt_text, taxon_image_alt_text)
        return

    logger.info("🟢 Posting:\n%s", message)
    status = post_to_mastodon(
        message,
        image_url=details.get("image_url"),
        taxon_image_url=details.get("taxon_image_url"),
        image_alt_text=image_alt_text,
        taxon_image_alt_text=taxon_image_alt_text,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    toot_id = str(status["id"]) if status else None

    try:
        record_post_extended(
            compound_qid=details["compound_qid"],
            taxon_qid=details["taxon_qid"],
            reference_qid=details["reference_qid"],
            compound_label=details["compound"],
            taxon_label=details["taxon"],
            reference_label=details["reference"],
            toot_id=toot_id,
            smiles=details.get("smiles"),
        )
    except OSError:
        logger.exception("⚠️ Posted toot %s, but failed to write extended log.", toot_id)
        return
    logger.info("✅ Posted and logged.")


def _try_candidate(
    qid: str,
    *,
    dry_run: bool,
    max_retries: int,
    retry_delay: float,
    counter_text: str | None = None,
    theme: Theme | None = None,
) -> str:
    """Attempt to fetch, compose, and publish a single candidate compound.

    Returns ``"posted"``, ``"skipped"``, or ``"failed"``.
    """
    logger.info("🔍 Trying compound %s...", qid)
    try:
        details = get_molecule_details(qid, theme=theme, max_retries=max_retries, retry_delay=retry_delay)
    except Exception as exc:
        logger.warning("⚠️ Failed to fetch details for %s: %s", qid, exc)
        return "failed"

    if not details:
        return "skipped"

    if was_posted(details["compound_qid"], details["taxon_qid"]):
        logger.info("⏩ Already posted %s + %s, skipping.", details["compound_qid"], details["taxon_qid"])
        return "skipped"

    message = _compose_candidate_message(details, counter_text=counter_text, theme=theme)
    if message is None:
        return "skipped"

    try:
        _publish_candidate(
            details,
            message,
            dry_run=dry_run,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
    except Exception as exc:
        logger.warning("⚠️ Failed to post candidate %s: %s", qid, exc)
        return "failed"
    return "posted"


def _resolve_counter_text(*, include_counter: bool) -> str | None:
    if not include_counter:
        return None
    try:
        stats = get_occurrence_stats()
        return format_counter_line(stats, for_next_post=True)
    except Exception as exc:
        logger.warning("⚠️ Unable to compute occurrence counter: %s", exc)
        return None


def _resolve_active_theme(theme: str | Theme | None, *, auto_theme: bool) -> Theme | None:
    if isinstance(theme, Theme):
        return theme
    if isinstance(theme, str):
        return get_theme(theme)
    if auto_theme:
        return get_theme_for_date()
    return None


def _run_pass(
    *,
    dry_run: bool,
    use_cache: bool,
    max_retries: int,
    retry_delay: float,
    max_candidate_attempts: int,
    counter_text: str | None,
    theme: Theme | None,
) -> bool:
    try:
        qids = load_candidates(
            use_cache=use_cache,
            max_retries=max_retries,
            retry_delay=retry_delay,
            theme=theme,
        )
    except (SparqlQueryError, OSError):
        logger.exception("❌ Failed to retrieve candidate compound QIDs.")
        return False

    secrets.SystemRandom().shuffle(qids)

    failed_attempts = 0
    for qid in qids:
        outcome = _try_candidate(
            qid,
            dry_run=dry_run,
            max_retries=max_retries,
            retry_delay=retry_delay,
            counter_text=counter_text,
            theme=theme,
        )
        if outcome == "posted":
            return True
        if outcome == "failed":
            failed_attempts += 1
            if failed_attempts >= max_candidate_attempts:
                break
            if retry_delay > 0:
                time.sleep(min(retry_delay, 5.0))

    logger.error("❌ No new unique compound-taxon pair could be posted (failed attempts: %d).", failed_attempts)
    return False


def run(
    dry_run: bool = False,
    use_cache: bool = False,
    *,
    max_retries: int = 3,
    retry_delay: float = 5.0,
    max_candidate_attempts: int = 20,
    include_counter: bool = True,
    theme: str | Theme | None = None,
    auto_theme: bool = False,
    strict_theme: bool = False,
) -> bool:
    """Select an unposted natural product occurrence and publish (or preview) it.

    Parameters
    ----------
    dry_run : bool
        When ``True``, preview the post without publishing to Mastodon.
    use_cache : bool
        When ``True``, load candidate compound QIDs from ``candidates.json``.
    max_retries : int
        Maximum retry attempts per SPARQL/media operation.
    retry_delay : float
        Base delay in seconds between retries.
    max_candidate_attempts : int
        Maximum failed candidate attempts before aborting the run.
    include_counter : bool
        When ``True``, include the occurrence counter line in composed posts.
    theme : str | Theme | None
        Optional theme slug or :class:`~daily_lotus.themes.Theme` instance.
    auto_theme : bool
        When ``True`` and ``theme`` is ``None``, pick the weekday's scheduled theme.
    strict_theme : bool
        When ``True``, do not fall back to an unthemed post if the active theme yields none.

    Returns
    -------
    bool
        ``True`` if a post (or dry-run preview) succeeded, ``False`` otherwise.
    """
    try:
        active_theme = _resolve_active_theme(theme, auto_theme=auto_theme)
    except UnknownThemeError:
        logger.exception("❌ Invalid theme requested.")
        return False

    counter_text = _resolve_counter_text(include_counter=include_counter)

    ok = _run_pass(
        dry_run=dry_run,
        use_cache=use_cache,
        max_retries=max_retries,
        retry_delay=retry_delay,
        max_candidate_attempts=max_candidate_attempts,
        counter_text=counter_text,
        theme=active_theme,
    )
    if ok or active_theme is None or strict_theme:
        return ok

    logger.warning(
        "⚠️ Thematic run for '%s' yielded no postable candidate; falling back to unthemed occurrence.",
        active_theme.slug,
    )
    return _run_pass(
        dry_run=dry_run,
        use_cache=use_cache,
        max_retries=max_retries,
        retry_delay=retry_delay,
        max_candidate_attempts=max_candidate_attempts,
        counter_text=counter_text,
        theme=None,
    )


def _print_themes() -> None:
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for item in list_themes():
        schedule = weekdays[item.weekday] if item.weekday is not None else "on-demand"
        print(f"{item.slug:<12} [{schedule:<9}] {item.hashtag:<16} - {item.description}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Daily LOTUS bot.")
    parser.add_argument("--dry-run", action="store_true", help="Run the bot without posting to Mastodon.")
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Load candidate QIDs from candidates.json instead of querying Wikidata.",
    )
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries per SPARQL/media operation.")
    parser.add_argument("--retry-delay", type=float, default=5.0, help="Base delay in seconds between retries.")
    parser.add_argument(
        "--max-candidate-attempts",
        type=int,
        default=20,
        help="Max failed candidate attempts before giving up.",
    )
    parser.add_argument("--counter", action="store_true", help="Print occurrence counter summary and exit.")
    parser.add_argument("--no-counter", action="store_true", help="Omit occurrence counter from posted message.")
    parser.add_argument("--theme", type=str, default=None, help="Post an occurrence matching a specific theme slug.")
    parser.add_argument("--auto-theme", action="store_true", help="Automatically select theme by UTC weekday.")
    parser.add_argument(
        "--strict-theme",
        action="store_true",
        help="Fail instead of falling back to an unthemed post if thematic candidates yield no result.",
    )
    parser.add_argument("--list-themes", action="store_true", help="List available themes and exit.")
    add_logging_args(parser, "daily_lotus.log")
    args = parser.parse_args()
    configure_logging_from_args(args, "daily_lotus.log")

    if args.list_themes:
        _print_themes()
        sys.exit(0)

    if args.counter:
        print(format_counter_summary(get_occurrence_stats()))
        sys.exit(0)

    ok = run(
        dry_run=args.dry_run,
        use_cache=args.use_cache,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
        max_candidate_attempts=args.max_candidate_attempts,
        include_counter=not args.no_counter,
        theme=args.theme,
        auto_theme=args.auto_theme,
        strict_theme=args.strict_theme,
    )
    sys.exit(0 if ok else 1)
