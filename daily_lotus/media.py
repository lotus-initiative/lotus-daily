from io import BytesIO

import requests

USER_AGENT = "DailyLotusBot/0.1 (https://earthmetabolome.org/; contact@earthmetabolome.org)"


def fetch_image(url: str, *, timeout: int = 30) -> tuple[bytes, str]:
    """Download an image from ``url``, converting SVG responses to PNG bytes.

    Parameters
    ----------
    url : str
        Source image URL.
    timeout : int
        HTTP request timeout in seconds.

    Returns
    -------
    tuple[bytes, str]
        Raw image bytes and normalized MIME type.
    """
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
