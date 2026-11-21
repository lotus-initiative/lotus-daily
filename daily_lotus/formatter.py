import logging

logger = logging.getLogger(__name__)

MAX_TOOT_LENGTH = 500


class MessageTooLongError(ValueError):
    """Raised when a formatted post exceeds the character limit after all fallbacks."""

    def __init__(self) -> None:
        super().__init__("🧨 Message too long even after all shortening steps.")


def _choose_article(word: str) -> str:
    return "an" if word and word[0].lower() in "aeiou" else "a"


def compose_message(
    compound: str,
    compound_qid: str,
    taxon: str,
    taxon_qid: str,
    reference: str,
    reference_qid: str,
    taxon_emoji: str,
    kingdom_label: str,
    *,
    counter_text: str | None = None,
) -> str:
    """Compose a Mastodon status message within the 500-character limit.

    Parameters
    ----------
    compound : str
        Compound label.
    compound_qid : str
        Compound Wikidata QID.
    taxon : str
        Taxon label.
    taxon_qid : str
        Taxon Wikidata QID.
    reference : str
        Reference title/label.
    reference_qid : str
        Reference Wikidata QID.
    taxon_emoji : str
        Emoji representing the organism's kingdom.
    kingdom_label : str
        Kingdom label in English.
    counter_text : str | None
        Optional occurrence counter line dropped first if length exceeds 500 chars.

    Returns
    -------
    str
        Formatted Mastodon post text.
    """
    article = _choose_article(kingdom_label)
    full_reference = f"{reference} [https://www.wikidata.org/wiki/{reference_qid}]"
    short_reference = f"[https://www.wikidata.org/wiki/{reference_qid}]"

    footer = (
        f"✏️ This occurrence is available for curation on Wikidata "
        f"[https://www.wikidata.org/wiki/{compound_qid}#P703]. If you spot an error, feel free to improve it!"
    )
    hashtags = "#LOTUS #Wikidata #LinkedOpenData"

    def build(ref_text: str, *, include_counter: bool, include_hashtags: bool) -> str:
        counter_line = f"{counter_text}\n" if include_counter and counter_text else ""
        tags = f"#DailyNP #OpenScience {hashtags}" if include_hashtags else "#DailyNP #OpenScience"
        return (
            "📣 Natural Product Occurrence of the Day\n\n"
            f"🧪 {compound} [https://www.wikidata.org/wiki/{compound_qid}] is a molecule\n"
            f"found in {article} {taxon_emoji} {kingdom_label}, {taxon} [https://www.wikidata.org/wiki/{taxon_qid}]\n"
            f"📚 according to: {ref_text}\n\n"
            f"{footer}\n\n"
            f"{counter_line}"
            f"{tags}"
        )

    candidates = [
        (full_reference, True, True, None),
        (full_reference, False, True, "🧪 Removed counter line to fit length limit"),
        (full_reference, False, False, "🧪 Removed hashtags"),
        (short_reference, False, False, f"🧪 Removed # and shortened reference to {short_reference}"),
    ]

    seen: set[str] = set()
    for ref_text, use_counter, use_tags, note in candidates:
        candidate = build(ref_text, include_counter=use_counter, include_hashtags=use_tags)
        if candidate in seen:
            continue
        seen.add(candidate)
        if len(candidate) <= MAX_TOOT_LENGTH:
            if note:
                logger.info("%s (length: %d)", note, len(candidate))
            return candidate

    raise MessageTooLongError()
