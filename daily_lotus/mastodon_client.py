import logging
import os
import time
from io import BytesIO
from typing import Any

import requests
from dotenv import load_dotenv
from mastodon import Mastodon
from mastodon.errors import MastodonError

load_dotenv()

logger = logging.getLogger(__name__)

USER_AGENT = "DailyLotusBot/0.1 (https://earthmetabolome.org/; contact@earthmetabolome.org)"
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def get_client() -> Mastodon:
    return Mastodon(
        access_token=os.getenv("MASTODON_ACCESS_TOKEN"),
        api_base_url=os.getenv("MASTODON_API_BASE_URL"),
    )


def _is_retryable_request_error(exc: requests.RequestException) -> bool:
    response = getattr(exc, "response", None)
    if response is None:
        return True
    return response.status_code in _RETRYABLE_STATUS_CODES


def _download_image_once(url: str, *, timeout: int) -> tuple[bytes, str]:
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "image/jpeg")
    if url.lower().endswith(".svg") or "image/svg+xml" in content_type.lower():
        from cairosvg import svg2png

        png = BytesIO()
        svg2png(bytestring=response.content, write_to=png)
        return png.getvalue(), "image/png"
    mime_type = content_type.split(";", 1)[0].strip() or "image/jpeg"
    return response.content, mime_type


def fetch_image(
    url: str,
    *,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: int = 30,
) -> tuple[bytes, str]:
    """Download an image from ``url``, converting SVGs to PNG with retries.

    Parameters
    ----------
    url : str
        Source image URL.
    max_retries : int
        Maximum download attempts on transient failures.
    retry_delay : float
        Base delay in seconds for exponential backoff between attempts.
    timeout : int
        HTTP request timeout in seconds.

    Returns
    -------
    tuple[bytes, str]
        Raw image bytes and normalized MIME type.
    """
    attempts = max(1, max_retries)
    for attempt in range(attempts):
        try:
            return _download_image_once(url, timeout=timeout)
        except requests.RequestException as exc:
            if not _is_retryable_request_error(exc) or attempt + 1 >= attempts:
                raise
            logger.warning("Image fetch failed for %s (attempt %d/%d): %s", url, attempt + 1, attempts, exc)
            if retry_delay > 0:
                time.sleep(retry_delay * (2**attempt))

    raise RuntimeError(url)  # pragma: no cover


def _upload_media_with_retry(
    client: Mastodon,
    image_bytes: bytes,
    mime_type: str,
    alt_text: str | None,
    *,
    max_retries: int,
    retry_delay: float,
) -> Any:
    attempts = max(1, max_retries)
    for attempt in range(attempts):
        try:
            return client.media_post(BytesIO(image_bytes), mime_type=mime_type, description=alt_text)
        except (MastodonError, OSError, requests.RequestException) as exc:
            if attempt + 1 >= attempts:
                raise
            logger.warning("Mastodon media upload failed (attempt %d/%d): %s", attempt + 1, attempts, exc)
            if retry_delay > 0:
                time.sleep(retry_delay * (2**attempt))
    raise RuntimeError(mime_type)  # pragma: no cover


def post_to_mastodon(
    message: str,
    image_url: str | None = None,
    taxon_image_url: str | None = None,
    in_reply_to_id: str | None = None,
    image_alt_text: str | None = None,
    taxon_image_alt_text: str | None = None,
    *,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> Any:
    """Fetch images, upload media, and publish a status to Mastodon.

    Parameters
    ----------
    message : str
        Status text content.
    image_url : str | None
        Optional molecule depiction URL.
    taxon_image_url : str | None
        Optional taxon photograph URL.
    in_reply_to_id : str | None
        Optional parent toot ID when posting a reply.
    image_alt_text : str | None
        Alt text for the molecule image.
    taxon_image_alt_text : str | None
        Alt text for the taxon image.
    max_retries : int
        Maximum retry attempts for image fetching and media uploads.
    retry_delay : float
        Base delay in seconds for exponential backoff.

    Returns
    -------
    Any
        Mastodon status response dictionary.
    """
    pending_media: list[tuple[bytes, str, str | None]] = []
    if image_url:
        img_bytes, mime = fetch_image(image_url, max_retries=max_retries, retry_delay=retry_delay)
        pending_media.append((img_bytes, mime, image_alt_text))

    if taxon_image_url:
        img_bytes, mime = fetch_image(taxon_image_url, max_retries=max_retries, retry_delay=retry_delay)
        pending_media.append((img_bytes, mime, taxon_image_alt_text))

    client = get_client()
    media_ids = [
        _upload_media_with_retry(
            client,
            img_bytes,
            mime,
            alt_text,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        for img_bytes, mime, alt_text in pending_media
    ]

    return client.status_post(
        message,
        media_ids=media_ids if media_ids else None,
        in_reply_to_id=in_reply_to_id,
    )
