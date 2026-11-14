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
from app.images.reel_encoder import CATEGORY_BGM_ALIASES, create_static_reel, select_bgm_track
from app.images.image_composer import _normalise_display_text
from app.intelligence.cloud_ai_client import CloudAIResearcher
from app.intelligence.credential_pool import AIProviderError, RotatingCredentialPool
from app.intelligence.news_researcher import (
    COOLDOWN_EXCEPTIONS,
    NEWS_CATEGORIES,
    build_news_and_post_plan_prompt,
)
from app.pipeline import (
    COOLDOWN_HOURS,
    _generate_background,
    _get_event_summary,
    _is_posting_cooldown_active,
    _publish_gate_rejection_reason,
    _read_last_posted_at,
    _record_successful_instagram_post,
    _validate_generated_plan,
    run_pipeline,
)


def _approve_test_plan(plan: dict) -> dict:
    event_time = datetime.now(timezone.utc).isoformat()
    plan["research_metrics"] = {
        "candidate_count": 25,
        "unique_candidate_count": 18,
    }
    plan["selected_story"]["actual_event_at"] = event_time
    plan["publish_assessment"] = {
        "eligible": True,
        "india_wide_relevance": "VERY_HIGH",
        "public_interest": "VERY_HIGH",
        "public_attention": "VERY_HIGH",
        "recency": "VERY_HIGH",
        "significance": "VERY_HIGH",
        "uniqueness": "VERY_HIGH",
        "thought_provoking": True,
        "emotional_resonance": "VERY_HIGH",
        "human_stakes": "A verified development has meaningful effects on families across India.",
        "india_wide_impact_evidence": "Official data confirms the policy applies nationwide.",
        "emotional_resonance_evidence": "The confirmed outcome gives affected families reason for hope.",
        "actual_event_at": event_time,
        "significant_new_development": False,
        "new_development_summary": "",
        "reason": "A verified, high-impact development affects a broad audience.",
    }
    return plan


class TrendPipelineTests(unittest.TestCase):
    def setUp(self):
        self._temp_history = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self._temp_history.write(b'{"last_posted_at": null}\n')
        self._temp_history.close()
        self._temp_rotation = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self._temp_rotation.write(b'{"groq": 0, "cloudflare": 0}\n')
        self._temp_rotation.close()
        self._history_patch = patch("app.pipeline.POSTING_HISTORY_PATH", Path(self._temp_history.name))
        self._rotation_patch = patch("app.pipeline.CREDENTIAL_ROTATION_PATH", Path(self._temp_rotation.name))
        self._history_patch.start()
        self._rotation_patch.start()

    def tearDown(self):
        self._history_patch.stop()
        self._rotation_patch.stop()
        for path in (self._temp_history.name, self._temp_rotation.name):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_news_prompt_requires_category_shortlist_then_global_comparison(self):
        prompt = build_news_and_post_plan_prompt()

        self.assertIn("5 latest, trending, impactful, attention-drawing indian news", prompt.lower())
        self.assertIn("shortlisted candidate", prompt.lower())
        self.assertIn("last 15 news posted", prompt.lower())
        self.assertIn("last 3 hrs", prompt.lower())
        self.assertIn("the daily chronicle", prompt.lower())
        self.assertIn("like, share and follow", prompt.lower())
        self.assertIn("see description for more detail", prompt.lower())
        for category in (
            "Crime",
            "Politics",
            "Geopolitics",
            "Disaster",
            "Achievement",
            "BreakingNews",
            "Business",
            "Trends",
            "EmergencyReporting",
        ):
            self.assertIn(category, prompt)

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
        story = {"title": "CRPF Injured in Kulgam Attack"}

        self.assertEqual(_get_event_summary(story), story["title"])

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

    def test_groq_plan_retries_invalid_response_with_next_key(self):
        plan = {"selected_story": {"title": "A current India story"}, "slides": [{"headline": "Update"}]}
        client = CloudAIResearcher("groq", ["first-key", "second-key"], "openai/gpt-oss-20b")
        responses = [
            {"choices": [{"message": {"content": "not valid JSON"}}]},
            {"choices": [{"message": {"content": json.dumps(plan)}}]},
        ]
        keys_seen = []

        def fake_post(_payload, api_key):
            keys_seen.append(api_key)
            return responses.pop(0)

        with patch.object(client, "_post", side_effect=fake_post):
            result = client.research_and_generate_plan("Return a plan")

        self.assertEqual(result, plan)
        self.assertEqual(keys_seen, ["first-key", "second-key"])

    def test_groq_plan_validation_retries_only_the_plan_request(self):
        valid_plan = {
            "selected_story": {
                "title": "A verified current news development",
                "event_summary": "A verified current news development affects local residents",
            },
            "category": "Technology",
            "headline": "A Verified Current News Update",
            "image_body": (
                "The latest verified update changes the way affected people access this service, "
                "with officials confirming the main decision today. The next steps will determine "
                "how quickly people and businesses feel its practical impact across India."
            ),
            "background_image_prompt": "A clean editorial illustration with clear space for text.",
            "description": "A useful, factual description of the current news.",
            "hashtags": ["#CurrentStory", "#IndiaUpdate", "#PublicInterest", "#LatestNews", "#NewsExplained"],
            "sources": [{"url": "https://news.example/story"}],
        }
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
            plan_validator=_validate_generated_plan,
        )
        responses = [
            {"choices": [{"message": {"content": json.dumps({"slides": []})}}]},
            {"choices": [{"message": {"content": json.dumps(valid_plan)}}]},
        ]
        keys_seen = []

        def fake_post(_payload, api_key):
            keys_seen.append(api_key)
            return responses.pop(0)

        with patch.object(client, "_post", side_effect=fake_post):
            result = client.research_and_generate_plan("Return a plan")

        self.assertEqual(result, valid_plan)
        self.assertEqual(keys_seen, ["first-key", "second-key"])

    def test_plan_validator_enforces_category_description_and_sources(self):
        plan = {
            "selected_story": {
                "title": "A verified current news development",
                "event_summary": "A verified current news development affects local residents",
            },
            "category": "Technology",
            "headline": "A verified current news update",
            "image_body": (
                "The latest verified update changes the way affected people access this service, "
                "with officials confirming the main decision today. The next steps will determine "
                "how quickly people and businesses feel its practical impact across India."
            ),
            "background_image_prompt": "A relevant editorial illustration.",
            "description": "A factual news description.",
            "hashtags": ["#CurrentStory", "#IndiaUpdate", "#PublicInterest", "#LatestNews", "#NewsExplained"],
            "sources": [{"url": "https://news.example/story"}],
        }

        with self.assertRaisesRegex(ValueError, "supported news categories"):
            plan["category"] = "Public interest"
            _validate_generated_plan(plan)

        plan["category"] = "Technology"
        _validate_generated_plan(plan)

        plan["description"] = "news " * 501
        with self.assertRaisesRegex(ValueError, "500 words"):
            _validate_generated_plan(plan)

        plan["description"] = "A factual news description."
        plan["sources"] = []
        with self.assertRaisesRegex(ValueError, "at least one source"):
            _validate_generated_plan(plan)

    def test_plan_validator_requires_five_to_fifteen_unique_valid_hashtags(self):
        plan = {
            "selected_story": {
                "title": "A verified current news development",
                "event_summary": "A verified current news development affects local residents",
            },
            "category": "Technology",
            "headline": "A verified current news update",
            "image_body": (
                "The latest verified update changes the way affected people access this service, "
                "with officials confirming the main decision today. The next steps will determine "
                "how quickly people and businesses feel its practical impact across India."
            ),
            "background_image_prompt": "A relevant editorial illustration.",
            "description": "A factual news description.",
            "hashtags": ["#News", "#India", "#Technology", "#Update"],
            "sources": [{"url": "https://news.example/story"}],
        }
        with self.assertRaisesRegex(ValueError, "5 to 15 relevant hashtags"):
            _validate_generated_plan(plan)

        plan["hashtags"].append("#PublicInterest")
        _validate_generated_plan(plan)

        plan["hashtags"][-1] = "#news"
        with self.assertRaisesRegex(ValueError, "must be unique"):
            _validate_generated_plan(plan)

        plan["hashtags"][-1] = "#Hashtag With Spaces"
        with self.assertRaisesRegex(ValueError, "single valid hashtag"):
            _validate_generated_plan(plan)

    def test_plan_validator_rejects_repeated_main_keywords_from_history(self):
        plan = {
            "selected_story": {
                "title": "ISRO successfully test fires GSLV rocket engine",
                "event_summary": "ISRO tests GSLV rocket engine ahead of NVS 04 launch",
            },
            "category": "Technology",
            "headline": "ISRO Tests GSLV Engine",
            "image_body": (
                "ISRO successfully completed a ground test of the GSLV rocket engine, "
                "an important step toward preparing the launch vehicle for the upcoming "
                "navigation satellite mission and its planned launch schedule."
            ),
            "background_image_prompt": "A rocket engine editorial illustration.",
            "description": "A factual update. Like, share and follow",
            "hashtags": ["#ISRO", "#GSLV", "#Rocket", "#Space", "#India"],
            "sources": [{"url": "https://news.example/story"}],
        }

        with self.assertRaisesRegex(ValueError, "overlapping main keywords"):
            _validate_generated_plan(
                plan,
                ["ISRO test fires GSLV rocket engine ahead of NVS 04 launch"],
            )

        _validate_generated_plan(
            plan,
            ["ISRO announces a new Chandrayaan lunar science instrument"],
        )

    def test_publish_gate_requires_exceptional_ratings_and_fresh_actual_event(self):
        result = _approve_test_plan(
            {
                "selected_story": {
                    "title": "A significant current public development",
                    "event_summary": "A significant current public development changes services",
                },
            }
        )
        self.assertIsNone(_publish_gate_rejection_reason(result, []))

        result["publish_assessment"]["public_interest"] = "HIGH"
        self.assertIn(
            "Public interest assessment is below the publishing threshold.",
            _publish_gate_rejection_reason(result, []),
        )

        result["publish_assessment"]["public_interest"] = "VERY_HIGH"
        result["publish_assessment"]["india_wide_relevance"] = "HIGH"
        self.assertIn(
            "India wide relevance assessment is below the publishing threshold.",
            _publish_gate_rejection_reason(result, []),
        )
        result["publish_assessment"]["india_wide_relevance"] = "VERY_HIGH"
        result["publish_assessment"]["emotional_resonance"] = "HIGH"
        self.assertIn(
            "Emotional resonance assessment is below the publishing threshold.",
            _publish_gate_rejection_reason(result, []),
        )
        result["publish_assessment"]["emotional_resonance"] = "VERY_HIGH"
        result["publish_assessment"]["thought_provoking"] = False
        self.assertIn(
            "thought-provoking angle",
            _publish_gate_rejection_reason(result, []),
        )
        result["publish_assessment"]["thought_provoking"] = True
        result["publish_assessment"]["actual_event_at"] = "2020-01-01T00:00:00Z"
        self.assertIn(
            "older than 24 hours",
            _publish_gate_rejection_reason(result, []),
        )

    def test_rejected_publish_gate_skips_all_media_and_returns_success_summary(self):
        result = {
            "research_metrics": {
                "candidate_count": 12,
                "unique_candidate_count": 9,
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
            self.assertEqual(summary["research_metrics"]["candidate_count"], 12)
            self.assertEqual(summary["research_metrics"]["unique_candidate_count"], 9)
            self.assertFalse((run_dir / "post_plan.json").exists())
            self.assertFalse((run_dir / "caption.txt").exists())
            self.assertEqual(json.loads(headlines_path.read_text(encoding="utf-8")), previous_summaries)
            generate_background.assert_not_called()
            compose_image.assert_not_called()
            create_reel.assert_not_called()

    def test_cloudflare_image_retries_only_the_failed_generation(self):
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
                if len(credentials_seen) == 1:
                    raise AIProviderError("temporary failure")
                return path

        with patch("app.pipeline.CloudflareImageGenerator", FakeGenerator):
            result = _generate_background("illustration", output_path, pool, "test-model")

        self.assertEqual(result, output_path)
        self.assertEqual(
            credentials_seen,
            [("first-account", "first-token"), ("second-account", "second-token")],
        )
        self.assertEqual(next(pool.attempts()), ("first-account", "first-token"))

    def test_failed_cloudflare_generation_does_not_update_shortlisted_headlines(self):
        plan = {
            "selected_story": {
                "title": "A current India story",
                "event_summary": "A current India development affects local residents",
            },
            "category": "Technology",
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
                patch("app.pipeline._make_ai_client", return_value=FakeAI()),
                patch("app.pipeline.CloudflareImageGenerator", FailedImageGenerator),
            ):
                with self.assertRaisesRegex(AIProviderError, "all 1 configured"):
                    run_pipeline()

            self.assertEqual(
                json.loads(headlines_path.read_text(encoding="utf-8")),
                previous_summaries,
            )

    def test_groq_client_fails_over_after_cloudflare_524_timeout(self):
        plan = {"selected_story": {"title": "A current India story"}, "slides": [{"headline": "Update"}]}
        client = CloudAIResearcher(
            "groq",
            ["first-key", "second-key"],
            "openai/gpt-oss-20b",
        )
        timeout_response = SimpleNamespace(
            status_code=524,
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
            side_effect=[timeout_response, success_response],
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
                r"HTTP 429.*rate limit or exhausted quota.*GROQ_API_KEYS.*share quota",
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
                r"HTTP 401.*Groq rejected the API key.*GROQ_API_KEYS.*restart the app",
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
            "category": "Technology",
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
            with (
                patch("app.pipeline.settings", fake_settings),
                patch("app.pipeline.SHORTLISTED_HEADLINES_PATH", headlines_path),
                patch("app.pipeline._make_ai_client", return_value=FakeAI()),
                patch("app.pipeline.CloudflareImageGenerator", FakeImageGenerator),
                patch("app.pipeline.compose_news_image", side_effect=capture_image),
                patch("app.pipeline.create_static_reel", side_effect=capture_reel),
            ):
                run_dir = run_pipeline()

            summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["selection_method"], "groq_browser_search")
            self.assertEqual(summary["category"], "Technology")
            self.assertEqual(Path(summary["bgm_file"]).name, "Other.m4a")
            self.assertEqual(summary["public_video_url"], None)
            self.assertTrue((Path(summary["post_directory"]) / "news_image.jpg").is_file())
            self.assertTrue((Path(summary["post_directory"]) / "reel.mp4").is_file())
            self.assertFalse((run_dir / "google_news_rss.json").exists())
            self.assertFalse((run_dir / "candidates.json").exists())
            prompt, max_tokens = requests_seen[0]
            self.assertEqual(max_tokens, 9000)
            self.assertIn("browser search", prompt.lower())
            self.assertIn("5 latest, trending, impactful, attention-drawing indian news", prompt.lower())
            self.assertIn("shortlisted candidate", prompt.lower())
            self.assertIn("5-15 word", prompt.lower())
            self.assertIn("500 words", prompt.lower())
            self.assertIn("like, share and follow", prompt.lower())
            self.assertIn("the daily chronicle", prompt.lower())
            self.assertIn("see description for more detail", prompt.lower())
            self.assertIn("last 15 news posted", prompt.lower())
            self.assertIn("compare distinctive people, organizations", prompt.lower())
            self.assertIn("5-15 relevant hashtags", prompt.lower())
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

    def test_persistent_round_robin_rotation_groq(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "credential_rotation.json"
            tokens = ["token-1", "token-2", "token-3", "token-4"]

            # Run 1: selects token-1
            pool_run1 = RotatingCredentialPool(tokens, "Groq", state_file)
            self.assertEqual(pool_run1.next_credential(), "token-1")
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["groq"], 1)

            # Run 2: starts with token-2; simulated failure occurs so fallback to token-3
            pool_run2 = RotatingCredentialPool(tokens, "Groq", state_file)
            self.assertEqual(pool_run2.next_credential(), "token-2")
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["groq"], 2)
            self.assertEqual(pool_run2.next_credential(), "token-3")
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["groq"], 3)

            # Run 3: starts with token-4; wraps to 0
            pool_run3 = RotatingCredentialPool(tokens, "Groq", state_file)
            self.assertEqual(pool_run3.next_credential(), "token-4")
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["groq"], 0)

            # Run 4: wraps back to token-1
            pool_run4 = RotatingCredentialPool(tokens, "Groq", state_file)
            self.assertEqual(pool_run4.next_credential(), "token-1")
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["groq"], 1)

    def test_persistent_round_robin_rotation_cloudflare(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "credential_rotation.json"
            credentials = [("acc-1", "tok-1"), ("acc-2", "tok-2")]

            pool1 = RotatingCredentialPool(credentials, "Cloudflare", state_file)
            self.assertEqual(pool1.next_credential(), ("acc-1", "tok-1"))

            pool2 = RotatingCredentialPool(credentials, "Cloudflare", state_file)
            self.assertEqual(pool2.next_credential(), ("acc-2", "tok-2"))

            pool3 = RotatingCredentialPool(credentials, "Cloudflare", state_file)
            self.assertEqual(pool3.next_credential(), ("acc-1", "tok-1"))

    def test_posting_cooldown_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_file = Path(temp_dir) / "posting_history.json"

            # No previous post
            history_file.write_text('{"last_posted_at": null}', encoding="utf-8")
            self.assertFalse(_is_posting_cooldown_active(history_file))

            # Posted 1 hour ago (within 3h cooldown)
            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            history_file.write_text(json.dumps({"last_posted_at": one_hour_ago.isoformat()}), encoding="utf-8")
            self.assertTrue(_is_posting_cooldown_active(history_file, now=now))

            # Posted 4 hours ago (outside 3h cooldown)
            four_hours_ago = now - timedelta(hours=4)
            history_file.write_text(json.dumps({"last_posted_at": four_hours_ago.isoformat()}), encoding="utf-8")
            self.assertFalse(_is_posting_cooldown_active(history_file, now=now))

            # Recording new post
            _record_successful_instagram_post(history_file, posted_at=now)
            self.assertTrue(_is_posting_cooldown_active(history_file, now=now))

    def test_publish_gate_rules_cooldown_and_exceptions(self):
        base_result = {
            "research_metrics": {"candidate_count": 5, "unique_candidate_count": 5},
            "selected_story": {
                "title": "Major National Disaster Hits Coastal Region",
                "event_summary": "Major national disaster hits coastal region causing significant flooding and relief operations",
            },
            "publish_assessment": {
                "eligible": True,
                "cooldown_active": False,
                "cooldown_exception": "NONE",
                "reason": "Top impactful story of the day.",
            },
            "category": "Disaster",
            "headline": "Major Coastal Disaster Hits Region",
            "image_body": "Severe flooding and relief operations are underway across the affected coastal region today with emergency teams deployed to assist local residents and businesses immediately.",
            "background_image_prompt": "Editorial illustration of emergency relief operations.",
            "description": "Critical updates on the coastal disaster relief. Like, share and follow.",
            "hashtags": ["#Disaster", "#IndiaNews", "#Breaking", "#Relief", "#Updates"],
            "sources": [{"url": "https://example.com/disaster"}],
        }

        # Rule 1: Cooldown inactive -> Approved
        self.assertIsNone(_publish_gate_rejection_reason(base_result, [], posting_cooldown_active=False))

        # Rule 2: Cooldown active and routine event (cooldown_exception = NONE) -> Rejected
        base_result["publish_assessment"]["cooldown_exception"] = "NONE"
        rejection = _publish_gate_rejection_reason(base_result, [], posting_cooldown_active=True)
        self.assertIsNotNone(rejection)
        self.assertIn("Posting cooldown active", rejection)

        # Rule 2: Cooldown active with valid exception -> Approved
        for exception in (
            "BREAKING_NEWS",
            "NATIONWIDE_IMPACT",
            "DISASTER",
            "MAJOR_ACHIEVEMENT",
            "RARE_EVENT",
            "UNUSUAL_NUMBERS",
            "SAD_EVENT",
            "PUBLIC_AWARENESS",
            "MAJOR_CHANGE",
        ):
            base_result["publish_assessment"]["cooldown_exception"] = exception
            self.assertIsNone(
                _publish_gate_rejection_reason(base_result, [], posting_cooldown_active=True),
                f"Failed to approve valid exception: {exception}",
            )

        # Rule 2: Cooldown active - minor/small incidents strictly rejected
        base_result["publish_assessment"]["cooldown_exception"] = "BREAKING_NEWS"
        base_result["publish_assessment"]["is_minor_or_local_incident"] = True
        minor_rejection = _publish_gate_rejection_reason(base_result, [], posting_cooldown_active=True)
        self.assertIsNotNone(minor_rejection)
        self.assertIn("small crime, small event, or minor incident", minor_rejection)
        base_result["publish_assessment"]["is_minor_or_local_incident"] = False

        # Rule 2: Cooldown active - lacks nationwide impact strictly rejected
        base_result["publish_assessment"]["nationwide_impact"] = False
        impact_rejection = _publish_gate_rejection_reason(base_result, [], posting_cooldown_active=True)
        self.assertIsNotNone(impact_rejection)
        self.assertIn("lacks verified nationwide impact", impact_rejection)
        base_result["publish_assessment"]["nationwide_impact"] = True

        # Rule 3: Eligible false -> Rejected with reason
        base_result["publish_assessment"]["eligible"] = False
        base_result["publish_assessment"]["reason"] = "Routine event during cooldown."
        self.assertIn("Routine event during cooldown", _publish_gate_rejection_reason(base_result, [], posting_cooldown_active=True))

    def test_bgm_track_aliasing_and_mapping(self):
        # Direct matches
        self.assertEqual(select_bgm_track("Crime").name, "Crime.mp3")
        self.assertEqual(select_bgm_track("Politics").name, "Politics.mp3")
        self.assertEqual(select_bgm_track("Business").name, "Business.mp3")
        self.assertEqual(select_bgm_track("Geopolitics").name, "Geopolitics.mp3")

        # Aliases
        self.assertEqual(select_bgm_track("Disaster").name, "Major Sad Event.mp3")
        self.assertEqual(select_bgm_track("EmergencyReporting").name, "Major Sad Event.mp3")
        self.assertEqual(select_bgm_track("Trends").name, "Social trends.mp3")

        # Fallback to Other
        self.assertEqual(select_bgm_track("Other").name, "Other.m4a")
        self.assertEqual(select_bgm_track("BreakingNews").name, "Other.m4a")
        self.assertEqual(select_bgm_track("Achievement").name, "Other.m4a")

    def test_news_prompt_with_cooldown_active_enforces_exception_rules(self):
        prompt = build_news_and_post_plan_prompt(posting_cooldown_active=True)
        self.assertIn("POSTING COOLDOWN STATUS: ACTIVE", prompt)
        self.assertIn("BREAKING_NEWS", prompt)
        self.assertIn("NATIONWIDE_IMPACT", prompt)
        self.assertIn("DISASTER", prompt)
        self.assertIn("MAJOR_ACHIEVEMENT", prompt)
        self.assertIn("small crime", prompt.lower())
        self.assertIn("minor incident", prompt.lower())


if __name__ == "__main__":
    unittest.main()
