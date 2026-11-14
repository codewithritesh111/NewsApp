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
        reasoning_effort: str = "low",
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
        self.reasoning_effort = reasoning_effort

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

    @staticmethod
    def _normalize_plan_shape(plan: dict[str, Any]) -> dict[str, Any]:
        story = plan.get("selected_story")
        if not isinstance(story, dict):
            return plan

        plan_fields = (
            "category",
            "headline",
            "image_body",
            "background_image_prompt",
            "description",
            "hashtags",
            "sources",
        )
        moved_fields = []
        for field in plan_fields:
            if field in story:
                if field not in plan:
                    plan[field] = story[field]
                    moved_fields.append(field)
                del story[field]
        if moved_fields:
            log.warning(
                "[GROQ] Promoted plan fields nested under selected_story to the top level: %s.",
                ", ".join(moved_fields),
            )
        return plan

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
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Groq response did not contain message content.") from exc
        if isinstance(content, list):
            content = "\n".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            if finish_reason == "length":
                raise AIProviderError(
                    "Groq returned empty message content (finish_reason='length'); "
                    "the model exhausted its token limit during reasoning."
                )
            raise AIProviderError("Groq returned empty message content.")
        return content.strip()

    @staticmethod
    def _response_metadata(
        data: dict[str, Any],
        attempt: int,
        credential_count: int,
        completion_limit: int,
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {
                "attempt": attempt,
                "configured_key_count": credential_count,
                "requested_completion_token_limit": completion_limit,
                "response_type": type(data).__name__,
            }
        choices = data.get("choices")
        choice_metadata = []
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    message = {}
                content = message.get("content")
                if isinstance(content, str):
                    content_type = "string"
                    content_length = len(content)
                elif isinstance(content, list):
                    content_type = "list"
                    content_length = sum(
                        len(item["text"])
                        for item in content
                        if isinstance(item, dict) and isinstance(item.get("text"), str)
                    )
                else:
                    content_type = "null" if content is None else type(content).__name__
                    content_length = 0 if content is None else None
                tool_calls = message.get("tool_calls")
                tool_names = []
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        function = (
                            tool_call.get("function")
                            if isinstance(tool_call, dict)
                            else None
                        )
                        if isinstance(function, dict) and isinstance(function.get("name"), str):
                            tool_names.append(function["name"])
                choice_metadata.append(
                    {
                        "finish_reason": choice.get("finish_reason"),
                        "message_role": message.get("role"),
                        "content_present": "content" in message,
                        "content_type": content_type,
                        "content_length": content_length,
                        "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
                        "tool_names": tool_names,
                        "refusal_present": bool(message.get("refusal")),
                    }
                )
        usage = data.get("usage")
        if isinstance(usage, dict):
            token_usage = {
                key: value
                for key, value in usage.items()
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and type(value) is int
            }
            for detail_name in ("prompt_tokens_details", "completion_tokens_details"):
                details = usage.get(detail_name)
                if isinstance(details, dict):
                    token_usage[detail_name] = {
                        key: value
                        for key, value in details.items()
                        if type(value) is int
                    }
            usage = token_usage
        else:
            usage = None
        return {
            "attempt": attempt,
            "configured_key_count": credential_count,
            "requested_completion_token_limit": completion_limit,
            "response_id": data.get("id") if isinstance(data.get("id"), str) else None,
            "response_model": data.get("model") if isinstance(data.get("model"), str) else None,
            "choice_count": len(choices) if isinstance(choices, list) else None,
            "choices": choice_metadata,
            "usage": usage,
        }

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
                        "Follow the prompt exactly. Use browser_search; return only the "
                        "requested single-image Reel JSON. Never invent facts or sources."
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
        reasoning_effort = getattr(self, "reasoning_effort", "low")
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

        credential_count = self.credentials.credential_count
        last_http_error: _GroqHTTPError | None = None
        log.info(
            "[GROQ] Request model=%s; configured keys=%d; prompt_chars=%d; "
            "max_completion_tokens=%d.",
            self.model,
            credential_count,
            len(prompt),
            max_tokens,
        )
        for attempt in range(credential_count):
            api_key = self.credentials.next_credential()
            log.info("[GROQ] Request attempt %d/%d.", attempt + 1, credential_count)
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

            metadata = self._response_metadata(
                data,
                attempt + 1,
                credential_count,
                max_tokens,
            )
            log.info("[GROQ] Response metadata: %s", json.dumps(metadata, sort_keys=True))
            if raw_output_path is not None:
                raw_output_path.parent.mkdir(parents=True, exist_ok=True)
                raw_output_path.with_name("groq_response_metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            try:
                content = self._content(data)
            except AIProviderError as exc:
                log.error(
                    "[GROQ] Response attempt %d/%d was unusable (%s); key rotation only "
                    "retries HTTP status errors.",
                    attempt + 1,
                    credential_count,
                    exc,
                )
                raise
            if raw_output_path is not None:
                raw_output_path.write_text(content, encoding="utf-8")
            plan = self._normalize_plan_shape(self._extract_json(content))
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
