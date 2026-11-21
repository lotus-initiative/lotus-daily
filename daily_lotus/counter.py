import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daily_lotus.log import load_extended_log, load_log, write_json_atomic
from daily_lotus.wikidata_query import (
    QLEVER_ENDPOINT,
    SPARQL_PREFIXES,
    WD_ENDPOINT,
    SparqlQueryError,
    execute_sparql,
)

logger = logging.getLogger(__name__)

FALLBACK_TOTAL_OCCURRENCES = 522_000
DAYS_PER_YEAR = 365.25
DEFAULT_STATS_CACHE = Path("occurrence_stats.json")


@dataclass(frozen=True)
class OccurrenceStats:
    """Summary statistics for LOTUS occurrences in Wikidata and posting progress."""

    total_occurrences: int
    posted_count: int
    remaining_occurrences: int
    remaining_days: int
    remaining_years: float
    source: str
    fetched_at: str | None = None


def build_occurrence_stats(
    total_occurrences: int,
    posted_count: int,
    *,
    source: str,
    fetched_at: str | None = None,
) -> OccurrenceStats:
    """Construct :class:`OccurrenceStats` from total and posted counts.

    Parameters
    ----------
    total_occurrences : int
        Total known occurrences in LOTUS / Wikidata.
    posted_count : int
        Number of unique compound-taxon occurrences posted so far.
    source : str
        Origin of the total count (``"wikidata"``, ``"cache"``, ``"stale_cache"``, or ``"fallback"``).
    fetched_at : str | None
        ISO-8601 UTC timestamp when the live count was fetched.

    Returns
    -------
    OccurrenceStats
        Computed occurrence and remaining-duration metrics.
    """
    remaining = max(0, total_occurrences - posted_count)
    return OccurrenceStats(
        total_occurrences=total_occurrences,
        posted_count=posted_count,
        remaining_occurrences=remaining,
        remaining_days=remaining,
        remaining_years=remaining / DAYS_PER_YEAR,
        source=source,
        fetched_at=fetched_at,
    )


def fetch_total_occurrences(
    *,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> int:
    """Query the total number of LOTUS compound-taxon occurrences from SPARQL.

    Parameters
    ----------
    max_retries : int
        Maximum retry attempts per SPARQL endpoint.
    retry_delay : float
        Base delay in seconds between retries.

    Returns
    -------
    int
        Total number of compound-taxon pairs with a SMILES string in Wikidata.
    """
    query = f"""
    {SPARQL_PREFIXES}
    SELECT (COUNT(*) AS ?count) WHERE {{
      ?compound wdt:P233 [] ;
                wdt:P703 ?taxon .
    }}
    """
    raw = execute_sparql(
        [(QLEVER_ENDPOINT, query), (WD_ENDPOINT, query)],
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    bindings = raw.get("results", {}).get("bindings", [])
    if not bindings or "count" not in bindings[0]:
        raise SparqlQueryError()
    return int(bindings[0]["count"]["value"])


def count_posted_occurrences() -> int:
    """Count unique ``(compound_qid, taxon_qid)`` occurrences recorded across logs.

    Returns
    -------
    int
        Number of unique posted occurrences.
    """
    pairs: set[tuple[str, str]] = set(load_log())
    for entry in load_extended_log():
        compound_qid = entry.get("compound_qid")
        taxon_qid = entry.get("taxon_qid")
        if compound_qid and taxon_qid:
            pairs.add((compound_qid, taxon_qid))
    return len(pairs)


def _read_cached_stats(cache_path: Path | None) -> tuple[int, str] | None:
    if cache_path is None or not cache_path.exists():
        return None
    try:
        payload: Any = json.loads(cache_path.read_text(encoding="utf-8"))
        total = int(payload["total_occurrences"])
        fetched_at = str(payload["fetched_at"])
        datetime.fromisoformat(fetched_at)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if total <= 0:
        return None
    return total, fetched_at


def _cache_age_hours(fetched_at: str) -> float:
    parsed = datetime.fromisoformat(fetched_at)
    utc_dt = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return (datetime.now(tz=timezone.utc) - utc_dt).total_seconds() / 3600.0


def get_occurrence_stats(
    *,
    cache_path: Path | None = DEFAULT_STATS_CACHE,
    max_age_hours: float = 24.0,
    refresh: bool = False,
) -> OccurrenceStats:
    """Return occurrence statistics using cache, live SPARQL, or fallback count.

    Parameters
    ----------
    cache_path : Path | None
        Optional JSON cache file path. Pass ``None`` to disable caching.
    max_age_hours : float
        Maximum cache age in hours before querying live SPARQL.
    refresh : bool
        When ``True``, bypass any fresh cache and query live SPARQL.

    Returns
    -------
    OccurrenceStats
        Current occurrence and remaining-duration statistics.
    """
    posted = count_posted_occurrences()
    cached = _read_cached_stats(cache_path)

    if cached is not None and not refresh:
        cached_total, cached_fetched_at = cached
        if _cache_age_hours(cached_fetched_at) < max_age_hours:
            return build_occurrence_stats(
                cached_total,
                posted,
                source="cache",
                fetched_at=cached_fetched_at,
            )

    try:
        total = fetch_total_occurrences()
    except (SparqlQueryError, ValueError, KeyError) as exc:
        logger.warning("⚠️ Failed to fetch total occurrences: %s", exc)
        if cached is not None:
            cached_total, cached_fetched_at = cached
            return build_occurrence_stats(
                cached_total,
                posted,
                source="stale_cache",
                fetched_at=cached_fetched_at,
            )
        return build_occurrence_stats(
            FALLBACK_TOTAL_OCCURRENCES,
            posted,
            source="fallback",
            fetched_at=None,
        )

    fetched_at = datetime.now(tz=timezone.utc).isoformat()
    if cache_path is not None:
        try:
            write_json_atomic(
                cache_path,
                {
                    "total_occurrences": total,
                    "fetched_at": fetched_at,
                    "source": "wikidata",
                },
            )
        except OSError as exc:
            logger.warning("⚠️ Failed to write occurrence stats cache %s: %s", cache_path, exc)

    return build_occurrence_stats(total, posted, source="wikidata", fetched_at=fetched_at)


def format_counter_line(stats: OccurrenceStats, *, for_next_post: bool = False) -> str:
    """Format a compact single-line counter string suitable for Mastodon posts.

    Parameters
    ----------
    stats : OccurrenceStats
        Occurrence statistics.
    for_next_post : bool
        When ``True``, increment the displayed index for the post being composed.

    Returns
    -------
    str
        Compact counter line.
    """
    index = stats.posted_count + 1 if for_next_post else stats.posted_count
    remaining = max(0, stats.total_occurrences - index) if for_next_post else stats.remaining_occurrences
    years = remaining / DAYS_PER_YEAR
    return f"⏳ Occurrence #{index:,} / {stats.total_occurrences:,} (~{years:,.0f} yrs left)"


def format_counter_summary(stats: OccurrenceStats) -> str:
    """Format a multi-line human-readable occurrence counter report.

    Parameters
    ----------
    stats : OccurrenceStats
        Occurrence statistics.

    Returns
    -------
    str
        Multi-line summary report.
    """
    lines = [
        "📊 LOTUS Occurrence Counter",
        f"• Total occurrences in LOTUS (Wikidata): {stats.total_occurrences:,}",
        f"• Occurrences posted so far: {stats.posted_count:,}",
        f"• Remaining occurrences / days (at 1/day): {stats.remaining_occurrences:,} ({stats.remaining_days:,} days)",
        f"• Estimated time remaining: ~{stats.remaining_years:,.1f} years",
        f"• Data source: {stats.source}",
    ]
    if stats.fetched_at:
        lines.append(f"• Fetched at: {stats.fetched_at}")
    return "\n".join(lines)


def main() -> None:
    """CLI entry point for displaying LOTUS occurrence counter metrics."""
    parser = argparse.ArgumentParser(description="Display LOTUS occurrence counter and remaining days estimate.")
    parser.add_argument("--refresh", action="store_true", help="Force refresh from live SPARQL instead of cache.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print statistics as JSON.")
    args = parser.parse_args()

    stats = get_occurrence_stats(refresh=args.refresh)
    if args.as_json:
        print(json.dumps(asdict(stats), indent=2))
    else:
        print(format_counter_summary(stats))


if __name__ == "__main__":
    main()
