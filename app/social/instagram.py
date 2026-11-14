import re
import time
from urllib.parse import urlsplit

import requests


class InstagramPublishingError(RuntimeError):
    pass


class InstagramPublisher:
    def __init__(
        self,
        instagram_user_id: str,
        access_token: str,
        api_version: str,
        *,
        request_timeout: int = 30,
        status_timeout: int = 180,
        status_poll_interval: float = 2,
    ) -> None:
        if not instagram_user_id.isdigit():
            raise ValueError("Instagram professional account ID must contain only digits.")
        if not access_token.strip():
            raise ValueError("Instagram access token is required.")
        if not re.fullmatch(r"v\d+\.\d+", api_version):
            raise ValueError("Instagram Graph API version must have the form vNN.N.")

        self.instagram_user_id = instagram_user_id
        self.access_token = access_token
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.request_timeout = request_timeout
        self.status_timeout = status_timeout
        self.status_poll_interval = status_poll_interval

    def _request(self, method: str, resource: str, **kwargs: object) -> dict:
        url = f"{self.base_url}/{resource}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=self.request_timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise InstagramPublishingError(
                f"Instagram API request failed for {method} /{resource}: {exc}"
            ) from exc

        if not response.ok:
            try:
                error_data = response.json()
            except ValueError:
                error_data = {}
            error = error_data.get("error") if isinstance(error_data, dict) else None
            message = (
                error.get("message", "unknown API error")
                if isinstance(error, dict)
                else f"HTTP {response.status_code}"
            )
            raise InstagramPublishingError(
                f"Instagram API rejected {method} /{resource}: {message}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise InstagramPublishingError(
                f"Instagram API returned invalid JSON for {method} /{resource}."
            ) from exc
        if not isinstance(data, dict):
            raise InstagramPublishingError(
                f"Instagram API returned an invalid response for {method} /{resource}."
            )
        error = data.get("error")
        if isinstance(error, dict):
            raise InstagramPublishingError(
                f"Instagram API rejected {method} /{resource}: "
                f"{error.get('message', 'unknown API error')}"
            )
        return data

    @staticmethod
    def _required_id(response: dict, operation: str) -> str:
        item_id = response.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise InstagramPublishingError(
                f"Instagram API response did not include an ID for {operation}."
            )
        return item_id

    def _wait_until_ready(self, container_id: str) -> None:
        deadline = time.monotonic() + self.status_timeout
        while True:
            response = self._request(
                "GET",
                container_id,
                params={"fields": "status_code"},
            )
            status = response.get("status_code")
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise InstagramPublishingError(
                    f"Instagram media container {container_id} is {status.lower()}."
                )
            if time.monotonic() >= deadline:
                raise InstagramPublishingError(
                    f"Instagram media container {container_id} was not ready before timeout."
                )
            time.sleep(self.status_poll_interval)

    def publish_reel(self, video_url: str, caption: str) -> str:
        parsed_url = urlsplit(video_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("Instagram Reel video URL must be publicly accessible HTTPS.")
        container = self._request(
            "POST",
            f"{self.instagram_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
            },
        )
        container_id = self._required_id(container, "Reel creation")
        self._wait_until_ready(container_id)
        published = self._request(
            "POST",
            f"{self.instagram_user_id}/media_publish",
            data={"creation_id": container_id},
        )
        return self._required_id(published, "Reel publishing")
