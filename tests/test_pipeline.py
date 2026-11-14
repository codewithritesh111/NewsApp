import json
import math
import os
import struct
import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageChops

from app.config import Settings
from app.images.image_composer import compose_news_image, compose_slide
from app.images.reel_encoder import create_static_reel, select_bgm_track
from app.images.image_composer import _normalise_display_text
from app.intelligence.cloud_ai_client import CloudAIResearcher
from app.intelligence.credential_pool import AIProviderError, RotatingCredentialPool
from app.intelligence.news_researcher import NEWS_CATEGORIES, build_news_and_post_plan_prompt
from app.pipeline import (
    POSTING_COOLDOWN,
    _generate_background,
    _get_event_summary,
    _prepare_description,
    _posting_cooldown_rejection_reason,
    _publish_gate_rejection_reason,
    _validate_generated_plan_structure,
    _validate_research_result,
    run_pipeline,
)


def _approve_test_plan(plan: dict) -> dict:
    plan["research_metrics"] = {
        "candidate_count": 5,
        "unique_candidate_count": 5,
    }
    plan["publish_assessment"] = {
        "eligible": True,
        "cooldown_exception": "NONE",
        "cooldown_exception_evidence": "",
        "reason": "A verified, high-impact development affects a broad audience.",
    }
    return plan


class TrendPipelineTests(unittest.TestCase):
    def test_news_prompt_is_compact_and_covers_candidate_and_publish_rules(self):
        prompt = build_news_and_post_plan_prompt()
        cooldown_prompt = build_news_and_post_plan_prompt(posting_cooldown_active=True)
        history = [f"Published story {index}: Distinct event {index}" for index in range(15)]
        prompt_with_history = build_news_and_post_plan_prompt(history)

        self.assertIn("5 latest", prompt)
        self.assertIn("last 5 hours", prompt)
        self.assertIn("occurrence time, not article time", prompt)
        self.assertIn("all 15 latest posted stories", prompt)
        self.assertIn("compare candidates with all 15", prompt.lower())
        self.assertIn("one shared distinctive name", prompt.lower())
        self.assertIn("No Reel was posted within 3 hours", prompt)
        self.assertIn("A Reel was posted within 3 hours", cooldown_prompt)
        self.assertIn("UNUSUAL_NUMBERS", prompt)
        self.assertNotIn("see description for more detail", prompt.lower())
        self.assertIn("bright, vivid, colorful", prompt.lower())
        self.assertIn("no text, lettering, signage", prompt.lower())
        self.assertLess(len(prompt), 2700)
        for item in history:
            self.assertIn(item, prompt_with_history)
        for category in NEWS_CATEGORIES:
            self.assertIn(f'"{category}"', prompt)
            self.assertEqual(prompt.count(f'"{category}"'), 1)

    def test_research_result_requires_integer_candidate_counts(self):
        result = _approve_test_plan(
            {
                "selected_story": {
                    "title": "A current India news story",
                    "event_summary": "A current India news story affects local residents",
                },
            }
        )
        result["research_metrics"]["candidate_count"] = "five"
        result["publish_assessment"]["eligible"] = False

        with self.assertRaisesRegex(ValueError, "integer candidate counts"):
            _validate_research_result(result)

    def test_multiple_credentials_are_parsed_and_cloudflare_pairs_stay_aligned(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEYS": " key-one, key-two \nkey-three ",
                "GROQ_API_KEY": "legacy-key",
                "CLOUDFLARE_ACCOUNT_IDS": "account-one, account-two",
                "CLOUDFLARE_API_TOKENS": "token-one, token-two",
            },
        ):
            settings = Settings(
                groq_api_key="legacy-key",
                cloudflare_account_id="legacy-account",
                cloudflare_api_token="legacy-token",
            )

            self.assertEqual(
                settings.groq_api_keys,
                ("key-one", "key-two", "key-three"),
            )
            self.assertEqual(
                settings.cloudflare_credentials,
                (("account-one", "token-one"), ("account-two", "token-two")),
            )

    def test_comma_separated_legacy_groq_api_key_is_supported(self):
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEYS": "",
                "GROQ_API_KEY": " first-key, second-key \nthird-key ",
            },
        ):
            settings = Settings(groq_api_key="first-key, second-key\nthird-key")

            self.assertEqual(
                settings.groq_api_keys,
                ("first-key", "second-key", "third-key"),
            )

    def test_cloudflare_credential_lists_must_have_matching_lengths(self):
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_ACCOUNT_IDS": "account-one,account-two",
                "CLOUDFLARE_API_TOKENS": "token-one",
            },
        ):
            with self.assertRaisesRegex(ValueError, "same number"):
                Settings().cloudflare_credentials

    def test_comma_separated_legacy_cloudflare_settings_are_paired(self):
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_ACCOUNT_IDS": "",
                "CLOUDFLARE_API_TOKENS": "",
                "CLOUDFLARE_ACCOUNT_ID": "account-one, account-two,account-three",
                "CLOUDFLARE_API_TOKEN": "token-one, token-two,token-three",
            },
        ):
            settings = Settings(
                cloudflare_account_id="account-one, account-two,account-three",
                cloudflare_api_token="token-one, token-two,token-three",
            )

            self.assertEqual(
                settings.cloudflare_credentials,
                (
                    ("account-one", "token-one"),
                    ("account-two", "token-two"),
                    ("account-three", "token-three"),
                ),
            )

    def test_comma_separated_legacy_cloudflare_settings_require_matching_lengths(self):
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_ACCOUNT_IDS": "",
                "CLOUDFLARE_API_TOKENS": "",
                "CLOUDFLARE_ACCOUNT_ID": "account-one,account-two",
                "CLOUDFLARE_API_TOKEN": "token-one",
            },
        ):
            with self.assertRaisesRegex(ValueError, "same number"):
                Settings(
                    cloudflare_account_id="account-one,account-two",
                    cloudflare_api_token="token-one",
                ).cloudflare_credentials

    def test_event_summary_accepts_fifteen_words_with_ampersand(self):
        story = {
            "event_summary": (
                "Three CRPF personnel wounded when terrorists opened fire at a checkpoint "
                "in Kulgam, Jammu & Kashmir."
            ),
        }

        self.assertEqual(_get_event_summary(story), story["event_summary"])

    def test_event_summary_falls_back_to_selected_story_title(self):
        story = {"title": "CRPF Injured"}

        self.assertEqual(_get_event_summary(story), story["title"])

    def test_event_summary_accepts_any_non_empty_summary(self):
        self.assertEqual(_get_event_summary({"event_summary": "One word"}), "One word")

    def test_nonbreaking_hyphens_are_normalized_before_rendering(self):
        self.assertEqual(
            _normalise_display_text("Delhi’s Time\u2011Bound, red\u2011tape"),
            "Delhi’s Time-Bound, red-tape",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            background = root / "background.jpg"
            Image.new("RGB", (1080, 1350), color=(70, 90, 110)).save(background)

            compose_slide(
                background,
                root / "nonbreaking_hyphen.jpg",
                {"headline": "Delhi’s Time\u2011Bound Law", "body": "Cuts red\u2011tape.", "key_fact": ""},
                "INDIA NEWS",
                1,
                1,
            )
            compose_slide(
                background,
                root / "plain_hyphen.jpg",
                {"headline": "Delhi’s Time-Bound Law", "body": "Cuts red-tape.", "key_fact": ""},
                "INDIA NEWS",
                1,
                1,
            )

            with Image.open(root / "nonbreaking_hyphen.jpg") as first:
                first_pixels = first.copy()
            with Image.open(root / "plain_hyphen.jpg") as second:
                self.assertIsNone(ImageChops.difference(first_pixels, second).getbbox())

    def test_groq_client_parses_plan_only_json_response(self):
        plan = {
            "selected_story": {"title": "A current India story"},
            "slides": [{"headline": "The update"}],
        }
        client = CloudAIResearcher.__new__(CloudAIResearcher)
        client.credentials = RotatingCredentialPool(["test-key"], "Groq")
        client.model = "openai/gpt-oss-20b"
        response = {
            "choices": [
                {"message": {"content": json.dumps(plan)}},
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            raw_output = Path(temp_dir) / "groq_response.txt"
            with patch.object(client, "_post", return_value=response):
                result = client.research_and_generate_plan("Return a plan", 9000, raw_output)

            self.assertEqual(result, plan)
            self.assertEqual(json.loads(raw_output.read_text(encoding="utf-8")), plan)

    def test_groq_plan_does_not_retry_invalid_response_with_another_key(self):
        client = CloudAIResearcher("groq", ["first-key", "second-key"], "openai/gpt-oss-20b")
        response = {"choices": [{"message": {"content": "not valid JSON"}}]}
        keys_seen = []

        def fake_post(_payload, api_key):
            keys_seen.append(api_key)
            return response

        with patch.object(client, "_post", side_effect=fake_post):
            with self.assertRaisesRegex(AIProviderError, "did not return JSON"):
                client.research_and_generate_plan("Return a plan")

        self.assertEqual(keys_seen, ["first-key"])

    def test_groq_plan_validation_does_not_retry_with_another_key(self):
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
            plan_validator=_validate_generated_plan_structure,
        )
        content = json.dumps({"slides": []})
        response = {"choices": [{"message": {"content": content}}]}
        keys_seen = []

        def fake_post(_payload, api_key):
            keys_seen.append(api_key)
            return response

        with tempfile.TemporaryDirectory() as temp_dir:
            raw_output = Path(temp_dir) / "groq_response.txt"
            with patch.object(client, "_post", side_effect=fake_post):
                with self.assertRaisesRegex(
                    AIProviderError,
                    "invalid response structure: Groq Reel plan must include a selected_story object",
                ):
                    client.research_and_generate_plan("Return a plan", raw_output_path=raw_output)

            self.assertEqual(raw_output.read_text(encoding="utf-8"), content)

        self.assertEqual(keys_seen, ["first-key"])

    def test_plan_structure_leaves_content_rules_to_groq(self):
        plan = {
            "selected_story": {
                "title": "A verified current news development",
                "event_summary": "Brief",
            },
            "category": "Unsupported but still a string",
            "headline": "A verified current news update",
            "image_body": "Too short",
            "background_image_prompt": "A relevant editorial illustration.",
            "description": "news " * 501,
            "hashtags": ["#news", "#news", "not a valid hashtag"],
            "sources": [],
        }

        _validate_generated_plan_structure(plan)
        self.assertEqual(
            _prepare_description(plan),
            f"{plan['description'].strip()}\n\n{' '.join(plan['hashtags'])}",
        )

    def test_plan_structure_still_rejects_malformed_types(self):
        plan = {
            "selected_story": {"title": "A current India story"},
            "category": "Other",
            "headline": "Headline",
            "image_body": "Body",
            "background_image_prompt": "Prompt",
            "description": "Description",
            "hashtags": ["#India", 5],
        }

        with self.assertRaisesRegex(ValueError, "hashtags must be a list of strings"):
            _validate_generated_plan_structure(plan)

    def test_publish_gate_uses_candidate_eligibility_without_extra_ratings(self):
        result = _approve_test_plan(
            {
                "selected_story": {
                    "title": "A significant current public development",
                    "event_summary": "A significant current public development changes services",
                },
            }
        )
        self.assertIsNone(_publish_gate_rejection_reason(result))
        self.assertNotIn("actual_event_at", result["publish_assessment"])

    def test_publish_gate_leaves_duplicate_screening_to_groq(self):
        result = _approve_test_plan(
            {
                "selected_story": {
                    "title": "ISRO tests GSLV engine before NVS launch",
                    "event_summary": "ISRO test fires GSLV rocket engine ahead of NVS launch",
                },
            }
        )
        self.assertIsNone(_publish_gate_rejection_reason(result))

    def test_posting_cooldown_requires_wait_unless_story_meets_an_exception(self):
        now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
        last_posted_at = datetime(2026, 11, 13, 11, 0, tzinfo=timezone.utc)
        assessment = {
            "cooldown_exception": "NONE",
            "cooldown_exception_evidence": "",
        }

        self.assertIn(
            "last 3 hours",
            _posting_cooldown_rejection_reason(assessment, last_posted_at, now),
        )
        self.assertIsNone(
            _posting_cooldown_rejection_reason(assessment, last_posted_at, now + POSTING_COOLDOWN)
        )

        assessment["cooldown_exception"] = "SAD_EVENT"
        assessment["cooldown_exception_evidence"] = "Verified major disaster with significant loss of life."
        self.assertIsNone(
            _posting_cooldown_rejection_reason(assessment, last_posted_at, now)
        )

    def test_pipeline_skips_recurring_story_during_posting_cooldown(self):
        result = _approve_test_plan(
            {
                "selected_story": {
                    "title": "A recurring weekly public update",
                    "event_summary": "Officials issue a regular weekly public update",
                },
            }
        )
        result["publish_assessment"]["eligible"] = False
        result["publish_assessment"]["reason"] = "This routine weekly update can wait."

        class FakeAI:
            def research_and_generate_plan(self, _prompt, _max_tokens, raw_output_path):
                raw_output_path.write_text(json.dumps(result), encoding="utf-8")
                return result

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            headlines_path = output_dir / "shortlisted_headlines.json"
            headlines_path.write_text(
                json.dumps(["An unrelated previous headline"]),
                encoding="utf-8",
            )
            history_path = output_dir / "posting_history.json"
            history_path.write_text(
                json.dumps(
                    {
                        "last_posted_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            fake_settings = SimpleNamespace(
                output_dir=output_dir,
                groq_model="openai/gpt-oss-20b",
            )
            with (
                patch("app.pipeline.settings", fake_settings),
                patch("app.pipeline.SHORTLISTED_HEADLINES_PATH", headlines_path),
                patch("app.pipeline.POSTING_HISTORY_PATH", history_path),
                patch("app.pipeline._make_ai_client", return_value=FakeAI()),
                patch("app.pipeline._generate_background") as generate_background,
            ):
                run_dir = run_pipeline(require_publish_configuration=True)

            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "skipped_posting_cooldown")
            self.assertIn("last 3 hours", summary["skip_reason"])
            self.assertFalse((run_dir / "post_plan.json").exists())
            generate_background.assert_not_called()

    def test_rejected_publish_gate_skips_all_media_and_returns_success_summary(self):
        result = {
            "research_metrics": {
                "candidate_count": 5,
                "unique_candidate_count": 5,
            },
            "selected_story": {
                "title": "A routine corporate product announcement",
                "event_summary": "A company announced a routine consumer product launch",
            },
            "publish_assessment": {
                "eligible": False,
                "india_wide_relevance": "LOW",
                "public_interest": "LOW",
                "public_attention": "LOW",
                "recency": "HIGH",
                "significance": "LOW",
                "uniqueness": "HIGH",
                "thought_provoking": False,
                "emotional_resonance": "LOW",
                "human_stakes": "",
                "india_wide_impact_evidence": "",
                "emotional_resonance_evidence": "",
                "actual_event_at": datetime.now(timezone.utc).isoformat(),
                "significant_new_development": False,
                "new_development_summary": "",
                "event_frequency": "UNKNOWN",
                "frequency_evidence": "",
                "cooldown_exception": "NONE",
                "cooldown_exception_evidence": "",
                "reason": "The announcement has limited public impact.",
            },
        }

        class FakeAI:
            def research_and_generate_plan(self, _prompt, _max_tokens, raw_output_path):
                raw_output_path.write_text(json.dumps(result), encoding="utf-8")
                return result

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            headlines_path = output_dir / "shortlisted_headlines.json"
            previous_summaries = ["An unrelated previously published public event"]
            headlines_path.write_text(json.dumps(previous_summaries), encoding="utf-8")
            fake_settings = SimpleNamespace(
                output_dir=output_dir,
                groq_model="openai/gpt-oss-20b",
            )
            with (
                patch("app.pipeline.settings", fake_settings),
                patch("app.pipeline.SHORTLISTED_HEADLINES_PATH", headlines_path),
                patch("app.pipeline._make_ai_client", return_value=FakeAI()),
                patch("app.pipeline._generate_background") as generate_background,
                patch("app.pipeline.compose_news_image") as compose_image,
                patch("app.pipeline.create_static_reel") as create_reel,
            ):
                run_dir = run_pipeline(require_publish_configuration=True)

            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "skipped_publish_gate")
            self.assertEqual(summary["publish_decision"], "REJECTED")
            self.assertEqual(summary["research_metrics"]["candidate_count"], 5)
            self.assertEqual(summary["research_metrics"]["unique_candidate_count"], 5)
            self.assertFalse((run_dir / "post_plan.json").exists())
            self.assertFalse((run_dir / "caption.txt").exists())
            self.assertEqual(json.loads(headlines_path.read_text(encoding="utf-8")), previous_summaries)
            generate_background.assert_not_called()
            compose_image.assert_not_called()
            create_reel.assert_not_called()

    def test_cloudflare_image_does_not_retry_with_another_credential(self):
        pool = RotatingCredentialPool(
            [("first-account", "first-token"), ("second-account", "second-token")],
            "Cloudflare",
        )
        credentials_seen = []
        output_path = Path("background.jpg")

        class FakeGenerator:
            def __init__(self, account_id, api_token, _model):
                credentials_seen.append((account_id, api_token))

            def generate(self, _prompt, path):
                raise AIProviderError("temporary failure")

        with patch("app.pipeline.CloudflareImageGenerator", FakeGenerator):
            with self.assertRaisesRegex(AIProviderError, "selected round-robin credential"):
                _generate_background("illustration", output_path, pool, "test-model")
        self.assertEqual(
            credentials_seen,
            [("first-account", "first-token")],
        )
        self.assertEqual(pool.next_credential(), ("second-account", "second-token"))

    def test_round_robin_credential_index_persists_between_pool_instances(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "credential_rotation.json"
            credentials = ("first-key", "second-key", "third-key")
            first_pool = RotatingCredentialPool(credentials, "Groq", state_path)
            second_pool = RotatingCredentialPool(credentials, "Groq", state_path)

            self.assertEqual(first_pool.next_credential(), "first-key")
            self.assertEqual(second_pool.next_credential(), "second-key")
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {"groq": 2},
            )

    def test_failed_cloudflare_generation_does_not_update_shortlisted_headlines(self):
        plan = {
            "selected_story": {
                "title": "A current India story",
                "event_summary": "A current India development affects local residents",
            },
            "category": "Other",
            "headline": "A Current India Story",
            "image_body": (
                "The latest verified update changes the way affected people access this service, "
                "with officials confirming the main decision today. The next steps will determine "
                "how quickly people and businesses feel its practical impact across India."
            ),
            "background_image_prompt": "Editorial illustration",
            "description": "A clear update. Like, share and follow",
            "hashtags": ["#CurrentStory", "#IndiaUpdate", "#PublicInterest", "#LatestNews", "#NewsExplained"],
            "sources": [{"url": "https://news.example/story"}],
        }
        plan = _approve_test_plan(plan)
        previous_summaries = ["An earlier completed event affected many residents"]

        class FakeAI:
            def research_and_generate_plan(self, _prompt, _max_tokens, _raw_output_path):
                return plan

        class FailedImageGenerator:
            def __init__(self, _account_id, _api_token, _model):
                pass

            def generate(self, _prompt, _output_path):
                raise AIProviderError("Cloudflare unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            headlines_path = output_dir / "shortlisted_headlines.json"
            headlines_path.write_text(json.dumps(previous_summaries), encoding="utf-8")
            rotation_path = output_dir / "credential_rotation.json"
            fake_settings = SimpleNamespace(
                output_dir=output_dir,
                groq_model="openai/gpt-oss-20b",
                cloudflare_credentials=(("test-account", "test-token"),),
                cloudflare_account_id="test-account",
                cloudflare_api_token="test-token",
                cloudflare_model="test-model",
                brand_name="The Daily Chronicle",
                image_width=1080,
            )

            with (
                patch("app.pipeline.settings", fake_settings),
                patch("app.pipeline.SHORTLISTED_HEADLINES_PATH", headlines_path),
                patch("app.pipeline.CREDENTIAL_ROTATION_PATH", rotation_path),
                patch("app.pipeline._make_ai_client", return_value=FakeAI()),
                patch("app.pipeline.CloudflareImageGenerator", FailedImageGenerator),
            ):
                with self.assertRaisesRegex(AIProviderError, "selected round-robin credential"):
                    run_pipeline()

            self.assertEqual(
                json.loads(headlines_path.read_text(encoding="utf-8")),
                previous_summaries,
            )

    def test_groq_client_retries_http_error_with_the_next_key(self):
        plan = {"selected_story": {"title": "A current India story"}, "slides": [{"headline": "Update"}]}
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
        )
        server_error = SimpleNamespace(
            status_code=503,
            ok=False,
        )
        success_response = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {
                "choices": [{"message": {"content": json.dumps(plan)}}],
            },
        )

        with patch(
            "app.intelligence.cloud_ai_client.requests.post",
            side_effect=[server_error, success_response],
        ) as post:
            result = client.research_and_generate_plan("Return a plan")

        self.assertEqual(result, plan)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            [
                call.kwargs["headers"]["Authorization"]
                for call in post.call_args_list
            ],
            ["Bearer first-key", "Bearer second-key"],
        )

    def test_groq_client_does_not_retry_when_publish_checks_reject_story(self):
        rejected_plan = {
            "research_metrics": {
                "candidate_count": 5,
                "unique_candidate_count": 5,
            },
            "selected_story": {
                "title": "A current story that fails publishing checks",
            },
            "publish_assessment": {
                "eligible": False,
                "reason": "The selected story does not meet the publishing checks.",
                "cooldown_exception": "NONE",
                "cooldown_exception_evidence": "",
            },
        }
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
            plan_validator=_validate_research_result,
        )
        response = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {
                "choices": [
                    {"message": {"content": json.dumps(rejected_plan)}},
                ],
            },
        )

        with patch(
            "app.intelligence.cloud_ai_client.requests.post",
            return_value=response,
        ) as post:
            result = client.research_and_generate_plan("Return a plan")

        self.assertEqual(result, rejected_plan)
        self.assertEqual(post.call_count, 1)

    def test_groq_client_rotates_on_next_request_after_non_http_error(self):
        plan = {"selected_story": {"title": "A current India story"}, "slides": [{"headline": "Update"}]}
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
        )
        empty_response = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {"choices": [{"message": {"content": ""}}]},
        )
        success_response = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {
                "choices": [{"message": {"content": json.dumps(plan)}}],
            },
        )

        with patch(
            "app.intelligence.cloud_ai_client.requests.post",
            side_effect=[empty_response, success_response],
        ) as post:
            with self.assertRaisesRegex(AIProviderError, "empty message content"):
                client.research_and_generate_plan("Return a plan")
            result = client.research_and_generate_plan("Return a plan")

        self.assertEqual(result, plan)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            [
                call.kwargs["headers"]["Authorization"]
                for call in post.call_args_list
            ],
            ["Bearer first-key", "Bearer second-key"],
        )

    def test_groq_429_error_explains_quota_and_credential_configuration(self):
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
        )
        rate_limited = SimpleNamespace(status_code=429, ok=False)

        with patch(
            "app.intelligence.cloud_ai_client.requests.post",
            return_value=rate_limited,
        ) as post:
            with self.assertRaisesRegex(
                AIProviderError,
                r"HTTP 429.*trying all 2 configured credentials.*GROQ_API_KEYS.*share quota",
            ):
                client.research_and_generate_plan("Return a plan")

        self.assertEqual(post.call_count, 2)

    def test_groq_401_error_explains_key_rejection_and_rotation(self):
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
        )
        unauthorized = SimpleNamespace(status_code=401, ok=False)

        with patch(
            "app.intelligence.cloud_ai_client.requests.post",
            return_value=unauthorized,
        ) as post:
            with self.assertRaisesRegex(
                AIProviderError,
                r"HTTP 401.*trying all 2 configured credentials.*GROQ_API_KEYS.*restart the app",
            ):
                client.research_and_generate_plan("Return a plan")

        self.assertEqual(post.call_count, 2)

    def test_composer_renders_dense_copy_and_distinct_key_fact(self):
        body = (
            "The confirmed decision changes a real cost for Indian consumers. "
            "Here is the detail that matters and what people should watch next."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            background = root / "background.jpg"
            Image.new("RGB", (1080, 1350), color=(70, 90, 110)).save(background)

            with_fact = compose_slide(
                background,
                root / "with_fact.jpg",
                {"headline": "What changed for consumers", "body": body, "key_fact": "₹500 a month"},
                "INDIA NEWS",
                1,
                4,
            )
            without_fact = compose_slide(
                background,
                root / "without_fact.jpg",
                {"headline": "What changed for consumers", "body": body, "key_fact": ""},
                "INDIA NEWS",
                1,
                4,
            )

            with Image.open(with_fact) as image:
                self.assertEqual(image.size, (1080, 1350))
            with Image.open(with_fact) as first, Image.open(without_fact) as second:
                self.assertIsNotNone(ImageChops.difference(first, second).getbbox())

    def test_composer_places_channel_name_at_top_left(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            background = root / "background.jpg"
            Image.new("RGB", (1080, 1350), color=(30, 60, 90)).save(background)

            output = compose_slide(
                background,
                root / "cover.jpg",
                {"headline": "Generated cover", "body": "Cover details.", "key_fact": ""},
                "The Daily Chronicle",
                1,
                3,
            )

            with Image.open(output) as image:
                brand_badge_pixel = image.getpixel((100, 80))
                self.assertGreater(brand_badge_pixel[0], brand_badge_pixel[1] * 2)
                self.assertEqual(image.size, (1080, 1350))

    def test_composer_only_renders_channel_name_on_first_slide(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            background = root / "background.jpg"
            Image.new("RGB", (1080, 1350), color=(30, 60, 90)).save(background)
            slide = {"headline": "A story headline", "body": "Story details.", "key_fact": ""}

            first = compose_slide(
                background,
                root / "first.jpg",
                slide,
                "The Daily Chronicle",
                1,
                3,
            )
            second = compose_slide(
                background,
                root / "second.jpg",
                slide,
                "The Daily Chronicle",
                2,
                3,
            )
            second_with_other_brand = compose_slide(
                background,
                root / "second_other_brand.jpg",
                slide,
                "Different Channel Name",
                2,
                3,
            )

            with Image.open(first) as first_image, Image.open(second) as second_image:
                first_badge = first_image.crop((76, 54, 500, 112))
                second_badge_area = second_image.crop((76, 54, 500, 112))
                self.assertIsNotNone(ImageChops.difference(first_badge, second_badge_area).getbbox())
            with Image.open(second) as second_image, Image.open(second_with_other_brand) as other_image:
                self.assertIsNone(ImageChops.difference(second_image, other_image).getbbox())

    def test_composer_does_not_render_category_in_top_right(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            background = root / "background.jpg"
            Image.new("RGB", (1080, 1350), color=(70, 90, 110)).save(background)

            output = compose_slide(
                background,
                root / "category_free.jpg",
                {"headline": "A story headline", "body": "Story details.", "key_fact": ""},
                "INDIA NEWS",
                2,
                3,
            )
            with Image.open(output) as image:
                top_right = image.crop((500, 70, 1000, 112))
                self.assertLessEqual(len(top_right.getcolors(maxcolors=top_right.width * top_right.height)), 10)

    def test_browser_discovery_creates_single_image_and_static_reel(self):
        rendered_images = []
        requests_seen = []
        image_prompts = []
        generator_config = []

        class FakeAI:
            def research_and_generate_plan(self, prompt, max_tokens, raw_output_path):
                requests_seen.append((prompt, max_tokens))
                raw_output_path.write_text("mock response", encoding="utf-8")
                return plan

        class FakeImageGenerator:
            def __init__(self, _account_id, _api_token, _model):
                generator_config.append((_account_id, _api_token, _model))

            def generate(self, prompt, output_path):
                image_prompts.append(prompt)
                output_path.write_bytes(b"background")
                return output_path

        def capture_image(background, output, headline, body, brand, width, height):
            rendered_images.append(
                (headline, body, brand, width, height)
            )
            output.write_bytes(b"jpeg")
            return output

        def capture_reel(image_path, output_path, _audio_path):
            output_path.write_bytes(b"mp4")
            return output_path

        plan = {
            "selected_story": {
                "title": "A current India story",
                "event_summary": "A current India development affected local residents and public services",
                "url": "https://news.example/lead",
            },
            "headline": "A Current India Story",
            "category": "Other",
            "image_body": (
                "The latest verified update changes the way affected people access this service, "
                "with officials confirming the main decision today. The next steps will determine "
                "how quickly people and businesses feel its practical impact across India."
            ),
            "background_image_prompt": "Editorial illustration supporting the development",
            "description": "A clear, engaging update. Like, share and follow",
            "hashtags": ["#CurrentStory", "#IndiaUpdate", "#PublicInterest", "#LatestNews", "#NewsExplained"],
            "sources": [{"url": "https://news.example/lead"}],
        }
        plan = _approve_test_plan(plan)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            fake_settings = SimpleNamespace(
                output_dir=output_dir,
                groq_model="openai/gpt-oss-20b",
                cloudflare_account_id="test-account",
                cloudflare_api_token="test-token",
                cloudflare_model="test-model",
                brand_name="The Daily Chronicle",
                image_width=1080,
                cloudinary_settings=None,
            )
            headlines_path = output_dir / "shortlisted_headlines.json"
            previous_summaries = [
                f"Previously selected event {index} involved a major public news development"
                for index in range(15)
            ]
            headlines_path.write_text(json.dumps(previous_summaries), encoding="utf-8")
            rotation_path = output_dir / "credential_rotation.json"
            with (
                patch("app.pipeline.settings", fake_settings),
                patch("app.pipeline.SHORTLISTED_HEADLINES_PATH", headlines_path),
                patch("app.pipeline.CREDENTIAL_ROTATION_PATH", rotation_path),
                patch("app.pipeline._make_ai_client", return_value=FakeAI()),
                patch("app.pipeline.CloudflareImageGenerator", FakeImageGenerator),
                patch("app.pipeline.compose_news_image", side_effect=capture_image),
                patch("app.pipeline.create_static_reel", side_effect=capture_reel),
            ):
                run_dir = run_pipeline()

            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["selection_method"], "groq_browser_search")
            self.assertEqual(summary["category"], "Other")
            self.assertEqual(Path(summary["bgm_file"]).name, "Other.m4a")
            self.assertEqual(summary["public_video_url"], None)
            self.assertTrue((Path(summary["post_directory"]) / "news_image.jpg").is_file())
            self.assertTrue((Path(summary["post_directory"]) / "reel.mp4").is_file())
            self.assertFalse((run_dir / "google_news_rss.json").exists())
            self.assertFalse((run_dir / "candidates.json").exists())
            prompt, max_tokens = requests_seen[0]
            self.assertEqual(max_tokens, 3500)
            self.assertIn("browser_search", prompt.lower())
            self.assertIn("5 latest", prompt.lower())
            self.assertIn("select the best by recency", prompt.lower())
            self.assertIn("compare candidates with all 15", prompt.lower())
            self.assertIn("no reel was posted within 3 hours", prompt.lower())
            self.assertIn("500 words", prompt.lower())
            self.assertNotIn("see description for more detail", prompt.lower())
            self.assertIn("all 15 latest posted stories", prompt.lower())
            self.assertIn("5-15 unique", prompt.lower())
            self.assertIn(previous_summaries[0], prompt)
            generated_post_plan = json.loads(
                (Path(summary["post_directory"]) / "post_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                json.loads(headlines_path.read_text(encoding="utf-8")),
                previous_summaries[1:] + [
                    "A current India development affected local residents and public services"
                ],
            )
            self.assertEqual(len(rendered_images), 1)
            self.assertEqual(len(image_prompts), 1)
            self.assertIn("#CurrentStory", (Path(summary["post_directory"]) / "caption.txt").read_text(encoding="utf-8"))
            self.assertEqual(generator_config[0][0], "test-account")
            self.assertEqual(rendered_images[0], ("A Current India Story", plan["image_body"], "The Daily Chronicle", 1080, 1920))
            self.assertEqual(
                len(json.loads(headlines_path.read_text(encoding="utf-8"))),
                15,
            )
            self.assertEqual(summary["background_images_generated"], 1)

    def test_bgm_selection_ignores_case_and_spaces_and_uses_other_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bgm_dir = Path(temp_dir)
            (bgm_dir / "Major Sad Event.mp3").write_bytes(b"audio")
            (bgm_dir / "Other.m4a").write_bytes(b"audio")

            self.assertEqual(
                select_bgm_track("major sadevent", bgm_dir).name,
                "Major Sad Event.mp3",
            )
            self.assertEqual(
                select_bgm_track("Defence", bgm_dir).name,
                "Other.m4a",
            )
            self.assertEqual(
                select_bgm_track("Disaster", bgm_dir).name,
                "Major Sad Event.mp3",
            )

    def test_bgm_selection_requires_other_when_category_has_no_track(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bgm_dir = Path(temp_dir)
            (bgm_dir / "Politics.mp3").write_bytes(b"audio")

            with self.assertRaisesRegex(FileNotFoundError, "no Other fallback"):
                select_bgm_track("Defence", bgm_dir)

    def test_static_reel_encoder_outputs_a_15_second_vertical_mp4_with_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            still = root / "still.jpg"
            output = root / "reel.mp4"
            audio = root / "bgm.wav"
            Image.new("RGB", (1080, 1920), color=(30, 60, 90)).save(still)
            sample_rate = 44100
            with wave.open(str(audio), "wb") as track:
                track.setnchannels(1)
                track.setsampwidth(2)
                track.setframerate(sample_rate)
                track.writeframes(
                    b"".join(
                        struct.pack(
                            "<h",
                            int(8000 * math.sin(2 * math.pi * 440 * index / sample_rate)),
                        )
                        for index in range(sample_rate)
                    )
                )

            create_static_reel(still, output, audio)

            self.assertGreater(output.stat().st_size, 0)
            ffmpeg = __import__("imageio_ffmpeg").get_ffmpeg_exe()
            probe = __import__("subprocess").run(
                [ffmpeg, "-i", str(output)],
                capture_output=True,
                text=True,
            )
            self.assertIn("Video:", probe.stderr)
            self.assertIn("1080x1920", probe.stderr)
            self.assertIn("15.00", probe.stderr)
            self.assertIn("Audio:", probe.stderr)

    def test_single_image_composer_uses_vertical_canvas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            background = root / "background.jpg"
            output = root / "news.jpg"
            Image.new("RGB", (1080, 1350), color=(70, 90, 110)).save(background)

            compose_news_image(
                background,
                output,
                "A clear news headline",
                "A short, self-contained explanation of what happened and why it matters.",
                "The Daily Chronicle",
            )

            with Image.open(output) as image:
                self.assertEqual(image.size, (1080, 1920))
                self.assertGreater(sum(image.getpixel((1000, 200))), sum((70, 90, 110)))
                self.assertLess(image.getpixel((100, 1580))[0], image.getpixel((100, 1580))[1])


if __name__ == "__main__":
    unittest.main()
