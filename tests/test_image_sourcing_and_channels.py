"""Tests for 3-channel verification rule and real camera-captured image sourcing."""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from app.intelligence.news_researcher import (
    APPROVED_NEWS_CHANNELS,
    match_approved_channel,
    build_news_and_post_plan_prompt,
)
from app.images.web_image_search import (
    extract_og_image_from_html,
    is_photographic,
    download_image,
    DISALLOWED_DOMAINS,
    DISALLOWED_URL_KEYWORDS,
)
from app.pipeline import _validate_generated_plan


class TestChannelVerificationAndImageSourcing(unittest.TestCase):
    def test_approved_channels_count_and_matching(self):
        self.assertEqual(len(APPROVED_NEWS_CHANNELS), 15)
        # Check canonical matching
        self.assertEqual(match_approved_channel("Aaj Tak"), "Aaj Tak")
        self.assertEqual(match_approved_channel("aajtak"), "Aaj Tak")
        self.assertEqual(match_approved_channel("NDTV 24x7"), "NDTV")
        self.assertEqual(match_approved_channel("India Today TV"), "India Today TV")
        self.assertEqual(match_approved_channel("India Today"), "India Today TV")
        self.assertEqual(match_approved_channel("Times Now"), "Times Now")
        self.assertEqual(match_approved_channel("Republic Bharat"), "Republic TV")
        self.assertEqual(match_approved_channel("Zee News"), "Zee News")
        self.assertEqual(match_approved_channel("India TV"), "India TV")
        self.assertEqual(match_approved_channel("News18 India"), "News18 India")
        self.assertEqual(match_approved_channel("DD News"), "DD News")
        self.assertEqual(match_approved_channel("TV9 Bharatvarsh"), "TV9 Bharatvarsh")
        self.assertEqual(match_approved_channel("News24"), "News24")
        self.assertEqual(match_approved_channel("Times Now Navbharat"), "Times Now Navbharat")
        self.assertEqual(match_approved_channel("WION"), "WION")

        # Unknown channels return None
        self.assertIsNone(match_approved_channel("Unknown Daily Blog"))
        self.assertIsNone(match_approved_channel("TechCrunch"))

    def test_prompt_contains_15_channels_mandate(self):
        prompt = build_news_and_post_plan_prompt()
        self.assertIn("3-channel verification mandate", prompt.lower())
        for ch in APPROVED_NEWS_CHANNELS:
            self.assertIn(ch, prompt)

    def test_validate_plan_enforces_3_channels_when_present(self):
        base_plan = {
            "category": "BreakingNews",
            "headline": "Massive Historic Space Launch Today",
            "image_body": "ISRO launches its heaviest communication satellite into space successfully today marking a major milestone.",
            "image_search_query": "ISRO rocket launch camera photo",
            "description": "Engaging description explaining the story. Like, share and follow.",
            "hashtags": ["#News", "#ISRO", "#India", "#Space", "#Breaking"],
            "selected_story": {
                "title": "ISRO launches heavy rocket",
                "event_summary": "ISRO launches its heaviest satellite into space successfully today.",
            },
            "sources": [{"name": "NDTV", "title": "Article", "url": "https://ndtv.com"}],
        }

        # Valid with 3 approved channels
        valid_plan = dict(base_plan)
        valid_plan["verified_channels"] = ["Aaj Tak", "NDTV", "India Today"]
        _validate_generated_plan(valid_plan)

        # Invalid: only 2 approved channels
        invalid_plan = dict(base_plan)
        invalid_plan["verified_channels"] = ["Aaj Tak", "NDTV"]
        with self.assertRaises(ValueError) as ctx:
            _validate_generated_plan(invalid_plan)
        self.assertIn("at least 3 approved channels", str(ctx.exception))

        # Invalid: channels not in approved list
        unapproved_plan = dict(base_plan)
        unapproved_plan["verified_channels"] = ["Random Blog", "Tech Blog", "Local News"]
        with self.assertRaises(ValueError) as ctx:
            _validate_generated_plan(unapproved_plan)
        self.assertIn("at least 3 approved channels", str(ctx.exception))

    def test_extract_og_image_from_html(self):
        html1 = '<meta property="og:image" content="https://images.site.com/photo.jpg">'
        self.assertEqual(extract_og_image_from_html(html1), "https://images.site.com/photo.jpg")

        html2 = '<meta content="https://images.site.com/photo2.jpg" property="og:image">'
        self.assertEqual(extract_og_image_from_html(html2), "https://images.site.com/photo2.jpg")

        html3 = '<meta name="twitter:image" content="https://images.site.com/tw.jpg">'
        self.assertEqual(extract_og_image_from_html(html3), "https://images.site.com/tw.jpg")

        html_none = '<html><head><title>No image</title></head></html>'
        self.assertIsNone(extract_og_image_from_html(html_none))

    def test_is_photographic_validation(self):
        # 1. Flat color drawing image (low unique colors)
        flat_img = Image.new("RGB", (200, 200), color=(255, 0, 0))
        self.assertFalse(is_photographic(flat_img))

        # 2. Transparent clipart
        transparent_img = Image.new("RGBA", (200, 200), color=(0, 0, 0, 0))
        self.assertFalse(is_photographic(transparent_img))

        # 3. Rich photographic image simulation (gradient with noise -> high color diversity)
        import random
        photo_sim = Image.new("RGB", (200, 200))
        pixels = photo_sim.load()
        for x in range(200):
            for y in range(200):
                pixels[x, y] = (
                    (x + random.randint(0, 50)) % 256,
                    (y + random.randint(0, 50)) % 256,
                    (x * y + random.randint(0, 50)) % 256,
                )
        self.assertTrue(is_photographic(photo_sim))

    def test_download_image_rejects_disallowed_domains_and_keywords(self):
        tmp_path = Path("output/test_img.jpg")
        # Britannica rejected
        with self.assertRaises(ValueError) as ctx:
            download_image("https://cdn.britannica.com/48/cyclone.jpg", tmp_path)
        self.assertIn("known clipart/vector/stock provider", str(ctx.exception))

        # Satellite keyword rejected
        with self.assertRaises(ValueError) as ctx:
            download_image("https://example.com/satellite-spiral.jpg", tmp_path)
        self.assertIn("disallowed non-camera keyword", str(ctx.exception))

    def test_extract_video_from_html_variants(self):
        from app.images.web_image_search import extract_video_from_html

        # og:video
        html_og = '<html><head><meta property="og:video" content="https://videos.ndtv.com/clip1.mp4"></head></html>'
        self.assertEqual(extract_video_from_html(html_og), "https://videos.ndtv.com/clip1.mp4")

        # video source tag
        html_source = '<html><body><video><source src="https://videos.aajtak.in/stream.mp4" type="video/mp4"></video></body></html>'
        self.assertEqual(extract_video_from_html(html_source), "https://videos.aajtak.in/stream.mp4")

        # JSON-LD contentUrl
        html_jsonld = '<html><script type="application/ld+json">{"@type": "VideoObject", "contentUrl": "https://indiatoday.in/video.mp4"}</script></html>'
        self.assertEqual(extract_video_from_html(html_jsonld), "https://indiatoday.in/video.mp4")

    def test_compose_news_overlay_generates_rgba_image(self):
        import tempfile
        from app.images.image_composer import compose_news_overlay

        with tempfile.TemporaryDirectory() as td:
            out_overlay = Path(td) / "overlay.png"
            compose_news_overlay(
                out_overlay,
                headline="Breaking News Space Feat",
                body="ISRO achieved a monumental space milestone today.",
                brand="The Daily Chronicle",
                width=1080,
                height=1920,
                layout={"text_position": "top", "highlight_words": ["SPACE"]},
            )
            self.assertTrue(out_overlay.is_file())
            with Image.open(out_overlay) as img:
                self.assertEqual(img.size, (1080, 1920))
                self.assertEqual(img.mode, "RGBA")

    @patch("app.images.web_image_search.requests.get")
    def test_search_news_videos_parses_video_pages(self, mock_get):
        from app.images.web_image_search import search_news_videos

        search_html = (
            '<html><body>'
            '<a href="https://www.indiatoday.in/india/video/sample-news-clip-12345">Video 1</a>'
            '<a href="https://www.bing.com/search?q=test">Bing link</a>'
            '</body></html>'
        )
        article_html = (
            '<html><head><meta property="og:video" content="https://stream.indiatoday.in/clip.mp4"></head></html>'
        )

        mock_resp_search = MagicMock()
        mock_resp_search.ok = True
        mock_resp_search.text = search_html

        mock_resp_article = MagicMock()
        mock_resp_article.ok = True
        mock_resp_article.text = article_html

        mock_get.side_effect = [mock_resp_search, mock_resp_article]

        streams = search_news_videos("Cyclone Akvan", limit=5)
        self.assertEqual(streams, ["https://stream.indiatoday.in/clip.mp4"])

    @patch("app.images.web_image_search.search_news_videos")
    @patch("app.images.web_image_search.download_video")
    def test_fetch_news_video_falls_back_to_search(self, mock_download, mock_search):
        from app.images.web_image_search import fetch_news_video

        mock_search.return_value = ["https://stream.news.com/video.mp4"]
        mock_download.return_value = Path("output/news_video.mp4")

        result = fetch_news_video(
            video_url=None,
            article_url=None,
            sources_urls=None,
            output_path=Path("output/news_video.mp4"),
            search_query="ISRO rocket launch",
        )
        self.assertEqual(result, Path("output/news_video.mp4"))
        mock_search.assert_called_once_with("ISRO rocket launch", limit=6)
        mock_download.assert_called_once_with("https://stream.news.com/video.mp4", Path("output/news_video.mp4"))

    def test_prompt_contains_strict_5_hours_recency_condition(self):
        prompt = build_news_and_post_plan_prompt()
        self.assertIn("5-hour recency limit", prompt.lower())
        self.assertIn("5 hours strictly", prompt.lower())

    def test_parse_publication_time_relative_and_iso(self):
        from datetime import datetime, timezone, timedelta
        from app.pipeline import parse_publication_time

        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime(2026, 11, 21, 22, 0, 0, tzinfo=ist)

        # 2 hours ago
        dt2 = parse_publication_time("2 hours ago", current_time=now)
        self.assertEqual(dt2, datetime(2026, 11, 21, 20, 0, 0, tzinfo=ist))

        # 45 mins ago
        dt_m = parse_publication_time("45 mins ago", current_time=now)
        self.assertEqual(dt_m, datetime(2026, 11, 21, 21, 15, 0, tzinfo=ist))

        # ISO timestamp
        dt_iso = parse_publication_time("2026-11-21T19:30:00+05:30", current_time=now)
        self.assertEqual(dt_iso, datetime(2026, 11, 21, 19, 30, 0, tzinfo=ist))

    def test_validate_story_recency_enforces_5_hours(self):
        from app.pipeline import _validate_story_recency

        # Within 5 hours: passes
        fresh_story = {"title": "Fresh Breaking News", "published_at": "2 hours ago"}
        _validate_story_recency(fresh_story)

        # Older than 5 hours: raises ValueError
        old_story = {"title": "Old News", "published_at": "7 hours ago"}
        with self.assertRaises(ValueError) as ctx:
            _validate_story_recency(old_story)
        self.assertIn("older than 5 hours strictly", str(ctx.exception))

    def test_find_story_recency_issue_detects_old_web_date(self):
        from app.pipeline import _find_story_recency_issue

        # When skip_web=True, declared fresh passes
        fresh_story = {"title": "Fresh Breaking News", "published_at": "1 hour ago"}
        self.assertIsNone(_find_story_recency_issue(fresh_story, skip_web=True))

        # Declared old fails immediately
        old_story = {"title": "Old Story", "published_at": "12 hours ago"}
        issue = _find_story_recency_issue(old_story, skip_web=True)
        self.assertIsNotNone(issue)
        self.assertIn("12 hours ago", issue)

    def test_create_video_reel_strictly_requires_duration_between_20s_and_60s(self):
        import tempfile
        from app.images.reel_encoder import create_video_reel

        with tempfile.TemporaryDirectory() as td:
            video_file = Path(td) / "short.mp4"
            overlay_file = Path(td) / "overlay.png"
            output_file = Path(td) / "reel.mp4"
            video_file.write_bytes(b"dummy video")
            overlay_file.write_bytes(b"dummy overlay")

            # Case 1: Duration < 20s strictly rejected
            with patch("app.images.reel_encoder.inspect_video", return_value=(15.0, False)):
                with self.assertRaises(ValueError) as ctx:
                    create_video_reel(video_file, overlay_file, output_file)
                self.assertIn("outside required [20s, 60s] range", str(ctx.exception))

            # Case 2: Duration > 60s strictly rejected
            with patch("app.images.reel_encoder.inspect_video", return_value=(75.0, False)):
                with self.assertRaises(ValueError) as ctx:
                    create_video_reel(video_file, overlay_file, output_file)
                self.assertIn("outside required [20s, 60s] range", str(ctx.exception))

            # Case 3: Duration between 20s and 60s accepted without trimming
            with patch("app.images.reel_encoder.inspect_video", return_value=(38.5, True)):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stderr="")
                    create_video_reel(video_file, overlay_file, output_file)
                    self.assertTrue(mock_run.called)
                    cmd = mock_run.call_args[0][0]
                    # Verify target_duration is exactly 38.5 without trimming
                    self.assertIn("-t", cmd)
                    t_idx = cmd.index("-t")
                    self.assertEqual(cmd[t_idx + 1], "38.5")
                    # Verify no looping on the video input
                    self.assertNotIn("-stream_loop", cmd)

    def test_fetch_news_background_queries_open_internet_in_9_16_ratio(self):
        import tempfile
        from app.images.web_image_search import fetch_news_background

        with tempfile.TemporaryDirectory() as td:
            out_img = Path(td) / "bg.jpg"
            with patch("app.images.web_image_search.search_bing_images") as mock_bing:
                with patch("app.images.web_image_search.download_image") as mock_dl:
                    mock_bing.return_value = ["https://photos.openweb.org/space916.jpg"]
                    mock_dl.return_value = out_img

                    res = fetch_news_background(
                        image_url=None,
                        search_query="ISRO space launch",
                        output_path=out_img,
                        news_title="ISRO Launches Heavy Satellite",
                    )
                    self.assertEqual(res, out_img)
                    # Verify Bing Images was queried with aspect_tall=True for 9:16 vertical images
                    called_tall = any(
                        call.kwargs.get("aspect_tall") is True for call in mock_bing.call_args_list
                    )
                    self.assertTrue(called_tall)
                    # Verify search query includes 9:16 targeting
                    query_terms = [call.args[0] for call in mock_bing.call_args_list if call.args]
                    self.assertTrue(any("9:16" in q for q in query_terms))
                    # Verify download_image required portrait orientation
                    self.assertTrue(mock_dl.call_args.kwargs.get("require_portrait"))


if __name__ == "__main__":
    unittest.main()
