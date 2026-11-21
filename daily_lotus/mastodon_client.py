import os
from io import BytesIO
from typing import Any

from dotenv import load_dotenv
from mastodon import Mastodon

from daily_lotus.media import fetch_image

load_dotenv()


def get_client() -> Mastodon:
    return Mastodon(
        access_token=os.getenv("MASTODON_ACCESS_TOKEN"),
        api_base_url=os.getenv("MASTODON_API_BASE_URL"),
    )


def post_to_mastodon(
    message: str,
    image_url: str | None = None,
    taxon_image_url: str | None = None,
    in_reply_to_id: str | None = None,
    image_alt_text: str | None = None,  # Add alt-text parameter
    taxon_image_alt_text: str | None = None,  # Add alt-text for taxon image
) -> Any:
    client = get_client()
    media_ids = []

    def upload_image_from_url(url: str, alt_text: str | None) -> Any:
        image_bytes, mime_type = fetch_image(url)
        return client.media_post(BytesIO(image_bytes), mime_type=mime_type, description=alt_text)

    if image_url:
        media_ids.append(upload_image_from_url(image_url, alt_text=image_alt_text))

    if taxon_image_url:
        media_ids.append(upload_image_from_url(taxon_image_url, alt_text=taxon_image_alt_text))

    return client.status_post(
        message,
        media_ids=media_ids if media_ids else None,
        in_reply_to_id=in_reply_to_id,
    )
