import os
import re
from typing import Any

import requests
from dotenv import load_dotenv

from daily_lotus.media import fetch_image

load_dotenv()

LINKEDIN_API_BASE = "https://api.linkedin.com/rest"
DEFAULT_LINKEDIN_API_VERSION = "202601"
MAX_COMMENTARY_LENGTH = 3000
MAX_ALT_TEXT_LENGTH = 400

_RESERVED_CHARS = set(r"\|{}@[]()<>*_~#")
_PROTECTED_TOKEN_RE = re.compile(r"(https?://[^\s<>\"'\[\]]+|(?<!\w)#[A-Za-z][A-Za-z0-9]*)")


def is_linkedin_configured() -> bool:
    """Return ``True`` when both ``LINKEDIN_ACCESS_TOKEN`` and ``LINKEDIN_AUTHOR_URN`` are set.

    Returns
    -------
    bool
        Whether LinkedIn cross-posting credentials are configured.
    """
    token = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
    author_urn = (os.getenv("LINKEDIN_AUTHOR_URN") or "").strip()
    return bool(token and author_urn)


def _escape_plain_segment(segment: str) -> str:
    return "".join(f"\\{ch}" if ch in _RESERVED_CHARS else ch for ch in segment)


def escape_little_text(text: str) -> str:
    """Escape LinkedIn Little Text Format reserved characters while keeping URLs and hashtags intact.

    Parameters
    ----------
    text : str
        Input text that may contain chemical names (e.g., ``(-)-verbenone``),
        URLs, and standalone hashtags.

    Returns
    -------
    str
        Text safe for the ``commentary`` field of LinkedIn's ``/rest/posts`` endpoint.
    """
    parts = _PROTECTED_TOKEN_RE.split(text)
    escaped: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        if index % 2 == 1:
            escaped.append(part)
        else:
            escaped.append(_escape_plain_segment(part))
    return "".join(escaped)


def _choose_article(word: str) -> str:
    return "an" if word and word[0].lower() in "aeiou" else "a"


def _truncate_commentary(text: str, max_length: int = MAX_COMMENTARY_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    truncated = text[: max_length - 1].rstrip()
    while truncated.endswith("\\"):
        truncated = truncated[:-1]
    return f"{truncated}…"


def compose_linkedin_message(
    compound: str,
    compound_qid: str,
    taxon: str,
    taxon_qid: str,
    reference: str,
    reference_qid: str,
    taxon_emoji: str,
    kingdom_label: str,
    *,
    mastodon_url: str | None = None,
) -> str:
    """Compose a LinkedIn post commentary string within the 3,000-character limit.

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
    mastodon_url : str | None
        Optional canonical Mastodon status URL to cite as the authoritative origin.

    Returns
    -------
    str
        Escaped commentary ready for LinkedIn's Posts API.
    """
    article = _choose_article(kingdom_label)
    safe_compound = escape_little_text(compound)
    safe_taxon = escape_little_text(taxon)
    safe_kingdom = escape_little_text(kingdom_label)
    safe_reference = escape_little_text(reference)

    lines = [
        "📣 Natural Product Occurrence of the Day",
        "",
        f"🧪 {safe_compound} — https://www.wikidata.org/wiki/{compound_qid}",
        f"is a molecule found in {article} {taxon_emoji} {safe_kingdom}, {safe_taxon} — https://www.wikidata.org/wiki/{taxon_qid}",
        f"📚 According to: {safe_reference} — https://www.wikidata.org/wiki/{reference_qid}",
        "",
        (
            "✏️ This occurrence is curated in the frame of the LOTUS Initiative on Wikidata: "
            f"https://www.wikidata.org/wiki/{compound_qid}#P703 — if you spot an error, feel free to improve it!"
        ),
    ]
    if mastodon_url:
        lines.append(f"🐘 Read more & follow curation updates on Mastodon: {mastodon_url}")

    lines.extend(["", "#DailyNP #OpenScience #LOTUS #Wikidata #LinkedOpenData"])
    return _truncate_commentary("\n".join(lines))


def _headers(token: str, version: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": version,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def upload_image(
    image_bytes: bytes,
    mime_type: str,
    author_urn: str,
    token: str,
    version: str,
) -> str:
    """Initialize and upload an image binary to LinkedIn's Images API.

    Parameters
    ----------
    image_bytes : bytes
        Raw binary image payload (PNG/JPEG/GIF).
    mime_type : str
        MIME type sent as ``Content-Type`` during the binary PUT upload.
    author_urn : str
        LinkedIn author URN (``urn:li:organization:...`` or ``urn:li:person:...``).
    token : str
        LinkedIn OAuth bearer access token.
    version : str
        LinkedIn API version header (``YYYYMM``).

    Returns
    -------
    str
        Uploaded LinkedIn image URN (e.g., ``urn:li:image:...``).
    """
    init_resp = requests.post(
        f"{LINKEDIN_API_BASE}/images?action=initializeUpload",
        headers=_headers(token, version),
        json={"initializeUploadRequest": {"owner": author_urn}},
        timeout=30,
    )
    init_resp.raise_for_status()
    data = init_resp.json()["value"]
    upload_url = str(data["uploadUrl"])
    image_urn = str(data["image"])

    put_resp = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": mime_type,
        },
        data=image_bytes,
        timeout=60,
    )
    put_resp.raise_for_status()
    return image_urn


def post_to_linkedin(
    text: str,
    image_items: list[tuple[bytes, str, str]] | None = None,
    *,
    token: str | None = None,
    author_urn: str | None = None,
    version: str | None = None,
) -> dict[str, str | None]:
    """Publish a text, single-image, or multi-image post to LinkedIn.

    Parameters
    ----------
    text : str
        Post commentary (escaped Little Text Format, up to 3,000 characters).
    image_items : list[tuple[bytes, str, str]] | None
        Optional sequence of ``(image_bytes, mime_type, alt_text)`` tuples.
    token : str | None
        Optional LinkedIn access token (defaults to ``LINKEDIN_ACCESS_TOKEN``).
    author_urn : str | None
        Optional author URN (defaults to ``LINKEDIN_AUTHOR_URN``).
    version : str | None
        Optional LinkedIn API version (defaults to ``LINKEDIN_API_VERSION`` or ``"202601"``).

    Returns
    -------
    dict[str, str | None]
        Dictionary containing ``{"post_id": ...}`` from ``x-restli-id``.
    """
    resolved_token = token or os.environ["LINKEDIN_ACCESS_TOKEN"]
    resolved_author_urn = author_urn or os.environ["LINKEDIN_AUTHOR_URN"]
    resolved_version = version or os.getenv("LINKEDIN_API_VERSION") or DEFAULT_LINKEDIN_API_VERSION

    uploaded_images: list[dict[str, str]] = []
    for img_bytes, mime_type, alt_text in image_items or []:
        urn = upload_image(img_bytes, mime_type, resolved_author_urn, resolved_token, resolved_version)
        uploaded_images.append({"id": urn, "altText": alt_text[:MAX_ALT_TEXT_LENGTH]})

    payload: dict[str, Any] = {
        "author": resolved_author_urn,
        "commentary": _truncate_commentary(text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    if len(uploaded_images) == 1:
        payload["content"] = {"media": uploaded_images[0]}
    elif len(uploaded_images) > 1:
        payload["content"] = {"multiImage": {"images": uploaded_images}}

    resp = requests.post(
        f"{LINKEDIN_API_BASE}/posts",
        headers=_headers(resolved_token, resolved_version),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return {"post_id": resp.headers.get("x-restli-id")}


def post_occurrence_to_linkedin(
    text: str,
    *,
    image_url: str | None = None,
    taxon_image_url: str | None = None,
    image_alt_text: str = "",
    taxon_image_alt_text: str = "",
) -> dict[str, str | None]:
    """Fetch molecule/taxon images and publish a daily occurrence post to LinkedIn.

    Parameters
    ----------
    text : str
        Formatted LinkedIn commentary.
    image_url : str | None
        Optional molecule depiction image URL.
    taxon_image_url : str | None
        Optional taxon photograph URL.
    image_alt_text : str
        Alt text for the molecule depiction image.
    taxon_image_alt_text : str
        Alt text for the taxon image.

    Returns
    -------
    dict[str, str | None]
        Dictionary containing ``{"post_id": ...}``.
    """
    image_items: list[tuple[bytes, str, str]] = []
    if image_url:
        mol_bytes, mol_mime = fetch_image(image_url)
        image_items.append((mol_bytes, mol_mime, image_alt_text))
    if taxon_image_url:
        taxon_bytes, taxon_mime = fetch_image(taxon_image_url)
        image_items.append((taxon_bytes, taxon_mime, taxon_image_alt_text))

    return post_to_linkedin(text, image_items=image_items)
