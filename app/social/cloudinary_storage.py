from pathlib import Path
from urllib.parse import quote

import requests


class CloudinaryUploadError(RuntimeError):
    pass


class CloudinaryPublicMedia:
    def __init__(self, cloud_name: str, api_key: str, api_secret: str) -> None:
        if not all((cloud_name, api_key, api_secret)):
            raise ValueError("Cloudinary cloud name, API key, and API secret are required.")
        self.cloud_name = cloud_name
        self.api_key = api_key
        self.api_secret = api_secret

    def upload_video(self, file_path: Path, public_id: str) -> str:
        if not file_path.is_file():
            raise FileNotFoundError(f"Instagram Reel video not found: {file_path}")
        if not public_id or public_id.startswith("/") or "\\" in public_id:
            raise ValueError("Cloudinary public ID must be a non-empty relative path.")

        endpoint = (
            f"https://api.cloudinary.com/v1_1/{quote(self.cloud_name, safe='')}/video/upload"
        )
        try:
            with file_path.open("rb") as video_file:
                response = requests.post(
                    endpoint,
                    auth=(self.api_key, self.api_secret),
                    data={"public_id": public_id, "overwrite": "true"},
                    files={"file": (file_path.name, video_file, "video/mp4")},
                    timeout=(10, 180),
                )
        except requests.RequestException as exc:
            raise CloudinaryUploadError(
                f"Cloudinary Reel upload failed for {file_path.name}: {exc}"
            ) from exc
        except OSError as exc:
            raise CloudinaryUploadError(
                f"Could not read Reel video {file_path}: {exc}"
            ) from exc

        try:
            result = response.json()
        except ValueError as exc:
            raise CloudinaryUploadError(
                f"Cloudinary returned invalid JSON for {file_path.name} "
                f"(HTTP {response.status_code})."
            ) from exc

        if not response.ok:
            error = result.get("error", {}) if isinstance(result, dict) else {}
            message = error.get("message", f"HTTP {response.status_code}")
            raise CloudinaryUploadError(
                f"Cloudinary rejected Reel {file_path.name}: {message}"
            )

        secure_url = result.get("secure_url") if isinstance(result, dict) else None
        if not isinstance(secure_url, str) or not secure_url.startswith("https://"):
            raise CloudinaryUploadError(
                f"Cloudinary did not return a public HTTPS URL for {file_path.name}."
            )
        return secure_url
