import argparse
import json
import logging
from pathlib import Path

from app.config import settings
from app.intelligence.credential_pool import AIProviderError
from app.pipeline import _record_successful_instagram_post, run_pipeline
from app.social.cloudinary_storage import CloudinaryUploadError
from app.social.instagram import InstagramPublisher, InstagramPublishingError


def _require_instagram_settings() -> None:
    required_settings = {
        "INSTAGRAM_USER_ID": settings.instagram_user_id,
        "INSTAGRAM_ACCESS_TOKEN": settings.instagram_access_token,
        "INSTAGRAM_GRAPH_API_VERSION": settings.instagram_graph_api_version,
    }
    missing = [name for name, value in required_settings.items() if not value]
    if missing:
        raise ValueError(
            "Instagram publishing requires these .env settings: "
            + ", ".join(missing)
        )


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _publish_reel(
    publisher: InstagramPublisher,
    video_url: str,
    description: str,
    summary: dict,
    summary_path: Path,
) -> str:
    media_id = publisher.publish_reel(video_url, description)
    summary["instagram_media_id"] = media_id
    summary["instagram_published_at"] = _record_successful_instagram_post()
    summary["instagram_publishing"] = "PUBLISHED"
    _save_json(summary_path, summary)
    return media_id


def _publish_generated_pipeline_run() -> tuple[Path, str | None]:
    run_dir = run_pipeline(require_publish_configuration=True)
    summary_path = run_dir / "run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") == "skipped_publish_gate":
        return run_dir, None
    _require_instagram_settings()
    post_dir = Path(summary["post_directory"])
    video_url = summary.get("public_video_url")
    if not isinstance(video_url, str) or not video_url.startswith("https://"):
        raise ValueError(
            "The pipeline did not return a hosted Reel URL; Instagram publishing was skipped."
        )
    description = (post_dir / "caption.txt").read_text(encoding="utf-8").strip()
    publisher = InstagramPublisher(
        settings.instagram_user_id,
        settings.instagram_access_token,
        settings.instagram_graph_api_version,
    )
    media_id = _publish_reel(
        publisher,
        video_url,
        description,
        summary,
        summary_path,
    )
    return run_dir, media_id


def _publish_existing_reel(post_dir: Path) -> str:
    _require_instagram_settings()
    plan_path = post_dir / "post_plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(f"Reel plan not found: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    video_url = plan.get("public_video_url") if isinstance(plan, dict) else None
    if not isinstance(video_url, str) or not video_url.startswith("https://"):
        raise ValueError(
            "This Reel has no hosted video URL. Run the generation pipeline with "
            "Cloudinary configured before publishing."
        )
    caption_path = post_dir / "caption.txt"
    if not caption_path.is_file():
        raise FileNotFoundError(f"Reel description not found: {caption_path}")
    description = caption_path.read_text(encoding="utf-8").strip()
    publisher = InstagramPublisher(
        settings.instagram_user_id,
        settings.instagram_access_token,
        settings.instagram_graph_api_version,
    )
    parent_summary_path = post_dir.parent / "run_summary.json"
    summary_path = (
        parent_summary_path
        if parent_summary_path.is_file()
        else post_dir / "run_summary.json"
    )
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.is_file()
        else {"post_directory": str(post_dir)}
    )
    media_id = _publish_reel(
        publisher,
        video_url,
        description,
        summary,
        summary_path,
    )
    return media_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="India News AI Studio - Groq research, Cloudflare image, and Instagram Reel"
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Create research and a Reel plan without generating an image or video",
    )
    parser.add_argument(
        "--publish-instagram",
        action="store_true",
        help="Generate and publish a new Reel, or publish a hosted Reel from --post-dir",
    )
    parser.add_argument(
        "--post-dir",
        type=Path,
        help="Existing generated Reel directory to publish",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        if args.publish_instagram:
            if args.text_only:
                parser.error("--text-only cannot be combined with --publish-instagram.")
            if args.post_dir is None:
                run_dir, media_id = _publish_generated_pipeline_run()
                if media_id is None:
                    print(f"\nInstagram publishing intentionally skipped. Pipeline output: {run_dir}")
                    return
                print(f"\nInstagram Reel published. Media ID: {media_id}")
                print(f"Pipeline output: {run_dir}")
                return
            media_id = _publish_existing_reel(args.post_dir.resolve())
            print(f"\nInstagram Reel published. Media ID: {media_id}")
            return

        if args.post_dir is not None:
            parser.error("--post-dir requires --publish-instagram.")
        output = run_pipeline(args.text_only)
    except (
        AIProviderError,
        InstagramPublishingError,
        CloudinaryUploadError,
        OSError,
        ValueError,
        RuntimeError,
    ) as exc:
        logging.error("%s", exc)
        raise SystemExit(1) from None
    print(f"\nPipeline output: {output}")


if __name__ == "__main__":
    main()
