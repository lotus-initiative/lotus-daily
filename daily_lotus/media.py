import logging
import time
from io import BytesIO

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "DailyLotusBot/0.1 (https://earthmetabolome.org/; contact@earthmetabolome.org)"
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


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
