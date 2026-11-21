import logging

logger = logging.getLogger(__name__)

MAX_TOOT_LENGTH = 500
DEFAULT_HEADER = "📣 Natural Product Occurrence of the Day"


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
    header: str | None = None,
    theme_hashtag: str | None = None,
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
    header : str | None
        Optional thematic header replacing the default title line.
    theme_hashtag : str | None
        Optional theme-specific hashtag included alongside ``#DailyNP #OpenScience``.

    Returns
    -------
    str
        Formatted Mastodon post text.
    """
    resolved_header = header or DEFAULT_HEADER
    article = _choose_article(kingdom_label)
    full_reference = f"{reference} [https://www.wikidata.org/wiki/{reference_qid}]"
    short_reference = f"[https://www.wikidata.org/wiki/{reference_qid}]"

    footer = (
        f"✏️ This occurrence is available for curation on Wikidata "
        f"[https://www.wikidata.org/wiki/{compound_qid}#P703]. If you spot an error, feel free to improve it!"
    )
    secondary_hashtags = "#LOTUS #Wikidata #LinkedOpenData"

    def build(
        ref_text: str,
        *,
        include_counter: bool,
        include_secondary_tags: bool,
        include_theme_tag: bool,
    ) -> str:
        counter_line = f"{counter_text}\n" if include_counter and counter_text else ""
        tag_parts = ["#DailyNP", "#OpenScience"]
        if include_theme_tag and theme_hashtag:
            tag_parts.append(theme_hashtag)
        if include_secondary_tags:
            tag_parts.append(secondary_hashtags)
        return (
            f"{resolved_header}\n\n"
            f"🧪 {compound} [https://www.wikidata.org/wiki/{compound_qid}] is a molecule\n"
            f"found in {article} {taxon_emoji} {kingdom_label}, {taxon} [https://www.wikidata.org/wiki/{taxon_qid}]\n"
            f"📚 according to: {ref_text}\n\n"
            f"{footer}\n\n"
            f"{counter_line}"
            f"{' '.join(tag_parts)}"
        )

    candidates = [
        (full_reference, True, True, True, None),
        (full_reference, False, True, True, "🧪 Removed counter line to fit length limit"),
        (full_reference, False, False, True, "🧪 Removed secondary hashtags"),
        (full_reference, False, False, False, "🧪 Removed theme hashtag"),
        (short_reference, False, False, False, f"🧪 Removed # and shortened reference to {short_reference}"),
    ]

    seen: set[str] = set()
    for ref_text, use_counter, use_secondary, use_theme_tag, note in candidates:
        candidate = build(
            ref_text,
            include_counter=use_counter,
            include_secondary_tags=use_secondary,
            include_theme_tag=use_theme_tag,
        )
        if candidate in seen:
            continue
        seen.add(candidate)
        if len(candidate) <= MAX_TOOT_LENGTH:
            if note:
                logger.info("%s (length: %d)", note, len(candidate))
            return candidate

    raise MessageTooLongError()
