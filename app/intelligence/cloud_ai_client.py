"""Groq client for hosted browser research and single-image Reel plans."""
import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import requests

from app.intelligence.credential_pool import AIProviderError, RotatingCredentialPool

log = logging.getLogger(__name__)


class _GroqHTTPError(AIProviderError):
    def __init__(self, status_code: int):
        super().__init__(f"Groq HTTP {status_code}.")
        self.status_code = status_code


class CloudAIResearcher:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        provider: str,
        api_key: str | Sequence[str],
        model: str,
        plan_validator: Callable[[dict[str, Any]], None] | None = None,
        credential_state_path: Path | None = None,
    ):
        if (provider or "").strip().lower() != "groq":
            raise ValueError("This workflow requires Groq browser_search. Set AI_PROVIDER=groq.")
        api_keys = (api_key,) if isinstance(api_key, str) else tuple(api_key)
        self.credentials = RotatingCredentialPool(
            (key.strip() for key in api_keys if key.strip()),
            "Groq",
            credential_state_path,
        )
        if model not in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
            raise ValueError(
                "Groq browser_search requires openai/gpt-oss-20b or openai/gpt-oss-120b."
            )
        self.model = model
        self.plan_validator = plan_validator

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise AIProviderError(f"Groq did not return JSON: {text[:500]}")
            try:
                value = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise AIProviderError("Groq returned invalid JSON.") from exc
        if not isinstance(value, dict):
            raise AIProviderError("Groq JSON response must be an object.")
        return value

    def _post(self, payload: dict, api_key: str | None = None) -> dict:
        key = api_key if api_key is not None else getattr(self, "api_key", "")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            response = requests.post(self.URL, headers=headers, json=payload, timeout=240)
        except requests.RequestException as exc:
            raise AIProviderError("Groq request failed.") from exc
        if not response.ok:
            raise _GroqHTTPError(response.status_code)
        try:
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AIProviderError("Groq returned an invalid response body.") from exc

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Groq response did not contain message content.") from exc
        if isinstance(content, list):
            content = "\n".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("Groq returned empty message content.")
        return content.strip()

    def research_and_generate_plan(
        self,
        prompt: str,
        max_tokens: int = 9000,
        raw_output_path: Path | None = None,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a careful news researcher and Indian news editor. Use "
                        "browser_search to discover and research one current story, then "
                        "return only the requested single-image Reel plan as JSON. Never invent "
                        "facts or sources."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_completion_tokens": max_tokens,
            "stream": False,
            "tool_choice": "required",
            "tools": [{"type": "browser_search"}],
        }

        credential_count = self.credentials.credential_count
        last_http_error: _GroqHTTPError | None = None
        for attempt in range(credential_count):
            api_key = self.credentials.next_credential()
            try:
                data = self._post(payload, api_key)
            except _GroqHTTPError as exc:
                last_http_error = exc
                if attempt + 1 < credential_count:
                    log.warning(
                        "Groq returned HTTP %d with credential %d/%d; trying the next key.",
                        exc.status_code,
                        attempt + 1,
                        credential_count,
                    )
                continue

            content = self._content(data)
            if raw_output_path is not None:
                raw_output_path.parent.mkdir(parents=True, exist_ok=True)
                raw_output_path.write_text(content, encoding="utf-8")
            plan = self._extract_json(content)
            validator = getattr(self, "plan_validator", None)
            if validator is not None:
                try:
                    validator(plan)
                except ValueError as exc:
                    raise AIProviderError(
                        f"Groq returned an invalid response structure: {exc}"
                    ) from exc
            break
        else:
            if last_http_error is None:
                raise AIProviderError("Groq request failed without an HTTP response.")
            if last_http_error.status_code == 429:
                raise AIProviderError(
                    f"Groq returned HTTP 429 after trying all {credential_count} "
                    "configured credentials. Check Groq account limits and configure "
                    "active keys in GROQ_API_KEYS "
                    f"({credential_count} configured). Keys in the same organization "
                    "may share quota."
                ) from last_http_error
            if last_http_error.status_code == 401:
                raise AIProviderError(
                    f"Groq returned HTTP 401 after trying all {credential_count} "
                    "configured credentials. Check GROQ_API_KEYS (or GROQ_API_KEY), "
                    "replace rejected keys, and restart the app."
                ) from last_http_error
            raise AIProviderError(
                f"Groq request failed after trying all {credential_count} configured "
                f"credentials. Last error: {last_http_error}"
            ) from last_http_error

        return plan
