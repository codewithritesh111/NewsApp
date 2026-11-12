import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.config import Settings
from app.social.cloudinary_storage import CloudinaryPublicMedia, CloudinaryUploadError


class CloudinaryStorageTests(unittest.TestCase):
    def test_cloudinary_settings_are_optional_but_require_all_values_when_started(self):
        self.assertIsNone(
            Settings(
                cloudinary_cloud_name="",
                cloudinary_api_key="",
                cloudinary_api_secret="",
            ).cloudinary_settings
        )
        settings = Settings(
            cloudinary_cloud_name="cloud",
            cloudinary_api_key="key",
            cloudinary_api_secret="",
        )
        with self.assertRaisesRegex(ValueError, "CLOUDINARY_API_SECRET"):
            _ = settings.cloudinary_settings

    def test_upload_reel_uses_secure_cloudinary_video_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reel = Path(temp_dir) / "reel.mp4"
            reel.write_bytes(b"mp4")
            response = Mock()
            response.ok = True
            response.status_code = 200
            response.json.return_value = {
                "secure_url": "https://res.cloudinary.com/demo/video/upload/reel.mp4"
            }
            with patch("app.social.cloudinary_storage.requests.post", return_value=response) as post:
                url = CloudinaryPublicMedia("demo", "key", "secret").upload_video(
                    reel,
                    "run/post/reel",
                )

        self.assertEqual(
            post.call_args.args[0],
            "https://api.cloudinary.com/v1_1/demo/video/upload",
        )
        self.assertEqual(post.call_args.kwargs["auth"], ("key", "secret"))
        self.assertEqual(post.call_args.kwargs["data"]["public_id"], "run/post/reel")
        self.assertEqual(post.call_args.kwargs["files"]["file"][2], "video/mp4")
        self.assertEqual(url, "https://res.cloudinary.com/demo/video/upload/reel.mp4")

    def test_upload_reel_surfaces_cloudinary_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reel = Path(temp_dir) / "reel.mp4"
            reel.write_bytes(b"mp4")
            response = Mock()
            response.ok = False
            response.status_code = 401
            response.json.return_value = {"error": {"message": "Invalid credentials"}}
            with patch("app.social.cloudinary_storage.requests.post", return_value=response):
                with self.assertRaisesRegex(CloudinaryUploadError, "Invalid credentials"):
                    CloudinaryPublicMedia("demo", "key", "secret").upload_video(
                        reel,
                        "run/post/reel",
                    )


if __name__ == "__main__":
    unittest.main()
