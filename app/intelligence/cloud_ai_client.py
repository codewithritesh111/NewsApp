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


class CloudAIResearcher:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        provider: str,
        api_key: str | Sequence[str],
        model: str,
        plan_validator: Callable[[dict[str, Any]], None] | None = None,
    ):
        if (provider or "").strip().lower() != "groq":
            raise ValueError("This workflow requires Groq browser_search. Set AI_PROVIDER=groq.")
        api_keys = (api_key,) if isinstance(api_key, str) else tuple(api_key)
        self.credentials = RotatingCredentialPool(
            (key.strip() for key in api_keys if key.strip()), "Groq"
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
            raise AIProviderError(f"Groq HTTP {response.status_code}.")
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

        last_error: AIProviderError | None = None
        credential_count = self.credentials.credential_count
        for attempt, api_key in enumerate(self.credentials.attempts(), start=1):
            try:
                content = self._content(self._post(payload, api_key))
                plan = self._extract_json(content)
                validator = getattr(self, "plan_validator", None)
                if validator is not None:
                    try:
                        validator(plan)
                    except ValueError as exc:
                        raise AIProviderError("Groq returned an invalid single-image Reel plan.") from exc
                break
            except AIProviderError as exc:
                last_error = exc
                if attempt < credential_count:
                    log.warning(
                        "Groq request failed with credential %d/%d; trying the next configured credential.",
                        attempt,
                        credential_count,
                    )
        else:
            if last_error and "HTTP 429" in str(last_error):
                raise AIProviderError(
                    f"Groq returned HTTP 429 for all {credential_count} configured "
                    "credential(s), indicating a rate limit or exhausted quota. "
                    "Check Groq account limits and configure additional active keys "
                    "with GROQ_API_KEYS. Keys in the same organization may share quota."
                ) from last_error
            if last_error and "HTTP 401" in str(last_error):
                raise AIProviderError(
                    f"Groq returned HTTP 401 for all {credential_count} configured "
                    "credential(s), which means Groq rejected the API key(s). Create "
                    "a new active key in Groq, update GROQ_API_KEYS (or GROQ_API_KEY) "
                    "in .env, and restart the app. Do not reuse a key that was revoked."
                ) from last_error
            raise AIProviderError(
                f"Groq request failed with all {credential_count} configured "
                f"credential(s). Last error: {last_error}"
            ) from last_error

        if raw_output_path is not None:
            raw_output_path.parent.mkdir(parents=True, exist_ok=True)
            raw_output_path.write_text(content, encoding="utf-8")
        return plan
