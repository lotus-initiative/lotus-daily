import argparse
import json
import logging

from daily_lotus.logging_config import add_logging_args, configure_logging_from_args
from daily_lotus.wikidata_query import get_candidate_qids

logger = logging.getLogger("daily_lotus.generate_candidate_cache")


def main() -> None:
    logger.info("🧠 Fetching all candidate compound QIDs from Wikidata...")
    qids = get_candidate_qids()
    logger.info("✅ Retrieved %d candidates.", len(qids))

    with open("candidates.json", "w") as f:
        json.dump(qids, f, indent=2)

    logger.info("💾 Saved to candidates.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate local candidates.json cache from Wikidata.")
    add_logging_args(parser, "generate_candidate_cache.log")
    args = parser.parse_args()
    configure_logging_from_args(args, "generate_candidate_cache.log")
    main()
