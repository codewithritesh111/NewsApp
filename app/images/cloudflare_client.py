import base64
import binascii
from io import BytesIO
import logging
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

from app.intelligence.credential_pool import AIProviderError

log = logging.getLogger(__name__)


class CloudflareImageGenerator:
    def __init__(self, account_id: str, api_token: str, model: str):
        if not account_id or not api_token:
            raise ValueError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required.")
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self.headers = {"Authorization": f"Bearer {api_token}"}

    def generate(self, prompt: str, output_path: Path, steps: int = 4) -> Path:
        payload = {"prompt": prompt, "steps": max(4, min(int(steps), 8))}
        try:
            response = requests.post(self.url, headers=self.headers, json=payload, timeout=180)
        except requests.RequestException as exc:
            raise AIProviderError("Cloudflare image request failed.") from exc
        if not response.ok:
            raise AIProviderError(
                f"Cloudflare image generation failed (HTTP {response.status_code})."
            )

        content_type = response.headers.get("content-type", "")
        try:
            if "application/json" in content_type:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Cloudflare response must be a JSON object.")
                encoded = data.get("image")
                if not encoded and isinstance(data.get("result"), dict):
                    encoded = data["result"].get("image")
                if not encoded:
                    raise ValueError("Cloudflare response did not contain an image.")
                image_bytes = base64.b64decode(encoded, validate=True)
            else:
                image_bytes = response.content

            with Image.open(BytesIO(image_bytes)) as image:
                image.verify()
        except (
            binascii.Error,
            OSError,
            requests.RequestException,
            TypeError,
            UnidentifiedImageError,
            ValueError,
        ) as exc:
            raise AIProviderError("Cloudflare returned an invalid image response.") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path
