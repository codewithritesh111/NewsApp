import unittest
from unittest.mock import patch

from app.social.instagram import InstagramPublisher, InstagramPublishingError


class InstagramTests(unittest.TestCase):
    def test_publisher_creates_and_publishes_reel_without_comment(self):
        class FakeResponse:
            ok = True
            status_code = 200

            def __init__(self, data):
                self.data = data

            def json(self):
                return self.data

        responses = [
            FakeResponse({"id": "container-123"}),
            FakeResponse({"status_code": "FINISHED"}),
            FakeResponse({"id": "123456"}),
        ]
        with patch("app.social.instagram.requests.request", side_effect=responses) as request:
            publisher = InstagramPublisher("12345", "private-token", "v27.0")
            media_id = publisher.publish_reel(
                "https://res.cloudinary.com/demo/video/upload/reel.mp4",
                "A factual news update. Like, share and follow",
            )

        self.assertEqual(media_id, "123456")
        self.assertEqual(request.call_count, 3)
        create_call = request.call_args_list[0]
        self.assertEqual(
            create_call.kwargs["data"],
            {
                "media_type": "REELS",
                "video_url": "https://res.cloudinary.com/demo/video/upload/reel.mp4",
                "caption": "A factual news update. Like, share and follow",
                "share_to_feed": "true",
            },
        )
        self.assertEqual(
            create_call.kwargs["headers"]["Authorization"],
            "Bearer private-token",
        )
        self.assertEqual(
            create_call.kwargs["data"]["caption"],
            "A factual news update. Like, share and follow",
        )

    def test_publisher_rejects_non_https_video_urls(self):
        publisher = InstagramPublisher("12345", "private-token", "v27.0")
        with self.assertRaisesRegex(ValueError, "publicly accessible HTTPS"):
            publisher.publish_reel("http://example.com/reel.mp4", "caption")

    def test_publisher_sends_long_captions_without_local_length_validation(self):
        class FakeResponse:
            ok = True

            def __init__(self, data):
                self.data = data

            def json(self):
                return self.data

        caption = "x" * 2201
        responses = [
            FakeResponse({"id": "container-123"}),
            FakeResponse({"status_code": "FINISHED"}),
            FakeResponse({"id": "123456"}),
        ]
        with patch("app.social.instagram.requests.request", side_effect=responses) as request:
            publisher = InstagramPublisher("12345", "private-token", "v27.0")
            publisher.publish_reel(
                "https://res.cloudinary.com/demo/video/upload/reel.mp4",
                caption,
            )

        self.assertEqual(request.call_args_list[0].kwargs["data"]["caption"], caption)

    def test_publisher_rejects_non_ready_reel_container(self):
        class FakeResponse:
            ok = True

            @staticmethod
            def json():
                return {"status_code": "ERROR"}

        publisher = InstagramPublisher("12345", "private-token", "v27.0")
        with patch("app.social.instagram.requests.request", return_value=FakeResponse()):
            with self.assertRaisesRegex(InstagramPublishingError, "is error"):
                publisher._wait_until_ready("bad-container")

if __name__ == "__main__":
    unittest.main()
