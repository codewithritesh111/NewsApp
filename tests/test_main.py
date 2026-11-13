import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.main import main


class MainCliTests(unittest.TestCase):
    def test_publish_flag_runs_pipeline_and_publishes_reel_without_comment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            post_dir = Path(temp_dir) / "post"
            run_dir.mkdir()
            post_dir.mkdir()
            description = "A factual update. Like, share and follow"
            (post_dir / "caption.txt").write_text(description, encoding="utf-8")
            summary_path = run_dir / "run_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "post_directory": str(post_dir),
                        "public_video_url": "https://res.cloudinary.com/demo/reel.mp4",
                        "instagram_publishing": "NOT_PUBLISHED",
                    }
                ),
                encoding="utf-8",
            )
            fake_settings = SimpleNamespace(
                instagram_user_id="123456",
                instagram_access_token="private-token",
                instagram_graph_api_version="v27.0",
                cloudinary_settings=("cloud", "key", "secret"),
            )
            with (
                patch("sys.argv", ["app.main", "--publish-instagram"]),
                patch("app.main.settings", fake_settings),
                patch("app.main.run_pipeline", return_value=run_dir) as pipeline,
                patch("app.main.InstagramPublisher") as publisher_class,
            ):
                publisher_class.return_value.publish_reel.return_value = "12345"
                main()

            pipeline.assert_called_once_with(require_publish_configuration=True)
            publisher_class.return_value.publish_reel.assert_called_once_with(
                "https://res.cloudinary.com/demo/reel.mp4",
                description,
            )
            publisher_class.return_value.create_comment.assert_not_called()
            saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_summary["instagram_publishing"], "PUBLISHED")
            self.assertEqual(saved_summary["instagram_media_id"], "12345")

    def test_publish_flag_treats_gate_rejection_as_successful_skip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            run_dir.mkdir()
            (run_dir / "run_summary.json").write_text(
                json.dumps({"status": "skipped_publish_gate"}),
                encoding="utf-8",
            )
            with (
                patch("sys.argv", ["app.main", "--publish-instagram"]),
                patch(
                    "app.main.run_pipeline",
                    return_value=run_dir,
                ) as pipeline,
                patch("app.main.InstagramPublisher") as publisher_class,
            ):
                main()

            pipeline.assert_called_once_with(require_publish_configuration=True)
            publisher_class.assert_not_called()

if __name__ == "__main__":
    unittest.main()
