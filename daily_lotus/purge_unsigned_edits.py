import argparse
import logging
import time
from html.parser import HTMLParser
from typing import Any

from daily_lotus.logging_config import add_logging_args, configure_logging_from_args
from daily_lotus.mastodon_client import get_client

logger = logging.getLogger("daily_lotus.purge_unsigned_edits")

_DAILY_POST_MARKER = "Natural Product Occurrence of the Day"
_EDIT_MARKERS = (
    "This occurrence was edited on Wikidata:",
    "This occurrence is no longer listed on Wikidata.",
)
_SIGNED_MARKER = "Edited by:"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li"} and self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def html_to_text(content: str) -> str:
    """Extract plain text from Mastodon status HTML.

    Parameters
    ----------
    content : str
        HTML content of a Mastodon status.

    Returns
    -------
    str
        Plain text representation.
    """
    extractor = _HTMLTextExtractor()
    extractor.feed(content)
    extractor.close()
    return extractor.get_text()


def is_unsigned_edit_toot(status: dict[str, Any]) -> bool:
    """Return ``True`` if a Mastodon status is an unsigned edit notification.

    Original daily occurrence posts (containing ``"Natural Product Occurrence of the Day"``),
    boosts, and signed edit replies (containing ``"Edited by:"``) are never matched.

    Parameters
    ----------
    status : dict[str, Any]
        Mastodon status dictionary.

    Returns
    -------
    bool
        Whether the status matches an unsigned edit toot.
    """
    if status.get("reblog"):
        return False

    text = html_to_text(str(status.get("content", "")))
    if not text or _DAILY_POST_MARKER in text or _SIGNED_MARKER in text:
        return False

    return any(marker in text for marker in _EDIT_MARKERS) or "#dailynpedit" in text.lower()


def find_unsigned_edit_toots(
    client: Any,
    *,
    max_statuses: int | None = None,
) -> list[dict[str, Any]]:
    """Scan the authenticated account's statuses for unsigned edit toots.

    Parameters
    ----------
    client : Any
        Authenticated Mastodon client instance.
    max_statuses : int | None
        Optional cap on the number of statuses to inspect.

    Returns
    -------
    list[dict[str, Any]]
        Matching Mastodon status dictionaries.
    """
    account = client.account_verify_credentials()
    page: list[dict[str, Any]] | None = client.account_statuses(account["id"], limit=40)
    matches: list[dict[str, Any]] = []
    inspected = 0

    while page:
        for status in page:
            if max_statuses is not None and inspected >= max_statuses:
                return matches
            inspected += 1
            if is_unsigned_edit_toot(status):
                matches.append(status)
        page = client.fetch_next(page)

    return matches


def purge_unsigned_edit_toots(
    client: Any,
    *,
    execute: bool = False,
    delay: float = 1.0,
    limit: int | None = None,
    max_statuses: int | None = None,
) -> list[str]:
    """Identify and optionally delete unsigned edit toots from the bot account.

    Parameters
    ----------
    client : Any
        Authenticated Mastodon client instance.
    execute : bool
        When ``False`` (default), run in dry-run mode without deleting statuses.
    delay : float
        Delay in seconds between deletions when ``execute=True``.
    limit : int | None
        Optional maximum number of matching statuses to process.
    max_statuses : int | None
        Optional maximum number of account statuses to scan.

    Returns
    -------
    list[str]
        IDs of matched (or deleted, when ``execute=True``) statuses.
    """
    matches = find_unsigned_edit_toots(client, max_statuses=max_statuses)
    targets = matches[:limit] if limit is not None else matches
    logger.info("Found %d unsigned edit toot(s); processing %d.", len(matches), len(targets))

    processed_ids: list[str] = []
    for index, status in enumerate(targets):
        status_id = str(status["id"])
        created_at = status.get("created_at", "")
        snippet = " ".join(html_to_text(str(status.get("content", ""))).split())[:100]
        if execute:
            client.status_delete(status["id"])
            logger.info("🗑️ Deleted toot %s (%s): %s", status_id, created_at, snippet)
            if delay > 0 and index + 1 < len(targets):
                time.sleep(delay)
        else:
            logger.info("💤 [DRY RUN] Would delete toot %s (%s): %s", status_id, created_at, snippet)
        processed_ids.append(status_id)

    return processed_ids


def main() -> None:
    """CLI entry point for purging unsigned edit toots."""
    parser = argparse.ArgumentParser(
        description="Find and optionally purge unsigned edit notification toots on Mastodon.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete matched toots (defaults to dry-run preview).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of matched toots to delete/preview.",
    )
    parser.add_argument(
        "--max-statuses",
        type=int,
        default=None,
        help="Maximum number of account statuses to inspect.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between status deletions.",
    )
    add_logging_args(parser, "purge_unsigned_edits.log")
    args = parser.parse_args()
    configure_logging_from_args(args, "purge_unsigned_edits.log")

    client = get_client()
    purge_unsigned_edit_toots(
        client,
        execute=args.execute,
        delay=args.delay,
        limit=args.limit,
        max_statuses=args.max_statuses,
    )


if __name__ == "__main__":
    main()
