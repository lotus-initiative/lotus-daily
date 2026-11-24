import logging
import os
import time
from io import BytesIO
from typing import cast
from uuid import uuid4

import requests
from dotenv import load_dotenv
from mastodon import Mastodon
from mastodon.errors import MastodonAPIError, MastodonError, MastodonRatelimitError
from mastodon.return_types import MediaAttachment, Status

from daily_lotus.media import fetch_image

load_dotenv()

logger = logging.getLogger(__name__)

_STATUS_RETRY_WINDOW_SECONDS = 50 * 60


class StatusUnconfirmedError(RuntimeError):
    """Mastodon may have published a status without returning its ID."""

    def __init__(self) -> None:
        super().__init__("Mastodon did not confirm whether it published the status.")


def get_client() -> Mastodon:
    return Mastodon(
        access_token=os.getenv("MASTODON_ACCESS_TOKEN"),
        api_base_url=os.getenv("MASTODON_API_BASE_URL"),
    )


def _upload_media_with_retry(
    client: Mastodon,
    image_bytes: bytes,
    mime_type: str,
    alt_text: str | None,
    *,
    max_retries: int,
    retry_delay: float,
) -> MediaAttachment:
    attempts = max(1, max_retries)
    for attempt in range(attempts):
        try:
            return cast(
                MediaAttachment,
                client.media_post(BytesIO(image_bytes), mime_type=mime_type, description=alt_text),
            )
        except (MastodonError, OSError, requests.RequestException) as exc:
            if attempt + 1 >= attempts:
                raise
            logger.warning("Mastodon media upload failed (attempt %d/%d): %s", attempt + 1, attempts, exc)
            if retry_delay > 0:
                time.sleep(retry_delay * (2**attempt))
    raise RuntimeError(mime_type)  # pragma: no cover


def _status_was_rejected(error: Exception) -> bool:
    if isinstance(error, MastodonRatelimitError):
        return True
    if isinstance(error, MastodonAPIError) and len(error.args) > 1:
        code = error.args[1]
        return isinstance(code, int) and 400 <= code < 500 and code != 408
    return False


def _submit_status(
    client: Mastodon,
    message: str,
    media: list[MediaAttachment],
    in_reply_to_id: str | None,
    *,
    idempotency_key: str,
    max_retries: int,
    retry_delay: float,
) -> Status:
    attempts = max(1, max_retries)
    delay = min(max(0.0, retry_delay), 30.0)
    deadline = time.monotonic() + _STATUS_RETRY_WINDOW_SECONDS
    for attempt in range(attempts):
        try:
            status = cast(
                Status,
                client.status_post(
                    message,
                    media_ids=media or None,
                    in_reply_to_id=in_reply_to_id,
                    idempotency_key=idempotency_key,
                ),
            )
        except Exception as error:
            if _status_was_rejected(error):
                if attempt:
                    raise StatusUnconfirmedError() from error
                raise
            failure = error
        else:
            if status and status.get("id"):
                return status
            failure = StatusUnconfirmedError()
        if attempt + 1 == attempts or time.monotonic() + delay >= deadline:
            raise StatusUnconfirmedError() from failure
        logger.warning("Mastodon status unconfirmed (attempt %d/%d): %s", attempt + 1, attempts, failure)
        if delay:
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise StatusUnconfirmedError()


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
    idempotency_key: str | None = None,
) -> Status:
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
        Maximum attempts per download, upload or status submission.
    retry_delay : float
        Base delay in seconds for exponential backoff.
    idempotency_key : str | None
        Stable key for retries of a single logical status. Omit for a fresh status.

    Returns
    -------
    Status
        Confirmed Mastodon status.
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

    return _submit_status(
        client,
        message,
        media_ids,
        in_reply_to_id,
        idempotency_key=idempotency_key if idempotency_key is not None else uuid4().hex,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
