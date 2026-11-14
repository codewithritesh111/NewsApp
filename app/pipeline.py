import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.images.cloudflare_client import CloudflareImageGenerator
from app.images.image_composer import compose_news_image
from app.images.reel_encoder import (
    REEL_HEIGHT,
    REEL_WIDTH,
    create_static_reel,
    select_bgm_track,
)
from app.intelligence.cloud_ai_client import CloudAIResearcher
from app.intelligence.credential_pool import AIProviderError, RotatingCredentialPool
from app.intelligence.news_researcher import (
    COOLDOWN_EXCEPTIONS,
    build_news_and_post_plan_prompt,
)
from app.social.cloudinary_storage import CloudinaryPublicMedia

log = logging.getLogger(__name__)
SHORTLISTED_HEADLINES_PATH = Path(__file__).resolve().parents[1] / "shortlisted_headlines.json"
POSTING_HISTORY_PATH = Path(__file__).resolve().parents[1] / "posting_history.json"
MAX_SHORTLISTED_HEADLINES = 15
POSTING_COOLDOWN = timedelta(hours=3)
CREDENTIAL_ROTATION_PATH = Path(__file__).resolve().parents[1] / "credential_rotation.json"
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value or "news").strip("-").lower()[:55] or "news"


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_shortlisted_summaries(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        summaries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Shortlisted summaries file is invalid JSON: {path}") from exc
    if not isinstance(summaries, list) or any(
        not isinstance(summary, str) or not summary.strip() for summary in summaries
    ):
        raise ValueError(f"Shortlisted summaries file must contain a JSON array of non-empty strings: {path}")
    return [summary.strip() for summary in summaries[-MAX_SHORTLISTED_HEADLINES:]]


def _record_shortlisted_summary(path: Path, summary: str, previous_summaries: list[str]) -> None:
    _write_json(path, (previous_summaries + [summary])[-MAX_SHORTLISTED_HEADLINES:])


def _read_last_posted_at(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Posting history file is invalid JSON: {path}") from exc
    if not isinstance(history, dict):
        raise ValueError(f"Posting history file must contain a JSON object: {path}")
    last_posted_at = history.get("last_posted_at")
    if last_posted_at is None:
        return None
    parsed = _parse_event_timestamp(last_posted_at)
    if parsed is None:
        raise ValueError(f"Posting history last_posted_at must be a timezone-aware ISO 8601 value: {path}")
    return parsed


def _record_successful_instagram_post() -> str:
    posted_at = datetime.now(IST)
    _write_json(POSTING_HISTORY_PATH, {"last_posted_at": posted_at.isoformat()})
    return posted_at.isoformat()


def _get_event_summary(story: dict) -> str:
    summary = story.get("event_summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    for field in ("title", "trend_rationale"):
        fallback = story.get(field)
        if not isinstance(fallback, str) or not fallback.strip():
            continue
        return fallback.strip()

    raise ValueError(
        "Groq selected_story must include an event_summary or usable title."
    )


def _parse_event_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _posting_cooldown_rejection_reason(
    assessment: dict,
    last_posted_at: datetime | None,
    now: datetime | None = None,
) -> str | None:
    current_time = now or datetime.now(timezone.utc)
    if last_posted_at is None or current_time - last_posted_at >= POSTING_COOLDOWN:
        return None

    if (
        assessment["cooldown_exception"] in COOLDOWN_EXCEPTIONS[1:]
        and assessment["cooldown_exception_evidence"].strip()
    ):
        return None
    return (
        "A Reel was published within the last 3 hours, and this story has no "
        "evidence-backed exception."
    )


def _validate_research_result(
    result: dict,
) -> None:
    metrics = result.get("research_metrics")
    if not isinstance(metrics, dict):
        raise ValueError("Groq research result must include research_metrics.")
    candidate_count = metrics.get("candidate_count")
    unique_count = metrics.get("unique_candidate_count")
    if type(candidate_count) is not int or type(unique_count) is not int:
        raise ValueError("Groq research metrics must contain integer candidate counts.")

    assessment = result.get("publish_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("Groq research result must include a structured publish_assessment.")
    if type(assessment.get("eligible")) is not bool:
        raise ValueError("Publish assessment eligible must be a boolean.")
    if not isinstance(assessment.get("cooldown_exception"), str):
        raise ValueError("Publish assessment cooldown_exception must be a string.")
    for field in ("reason", "cooldown_exception_evidence"):
        if not isinstance(assessment.get(field), str):
            raise ValueError(f"Publish assessment {field} must be a string.")

    story = result.get("selected_story")
    if story is None:
        if assessment["eligible"]:
            raise ValueError("Groq cannot approve publishing when no story was selected.")
        return
    if not isinstance(story, dict) or not isinstance(story.get("title"), str) or not story["title"].strip():
        raise ValueError("Groq selected_story must include a non-empty title or be null.")
    if assessment["eligible"]:
        _validate_generated_plan_structure(result)


def _publish_gate_rejection_reason(
    result: dict,
) -> str | None:
    assessment = result["publish_assessment"]
    if not assessment["eligible"]:
        return assessment["reason"].strip() or "Groq rejected the story for publishing."
    if result.get("selected_story") is None:
        return "No candidate was selected."
    return None


def _validate_generated_plan_structure(plan: dict) -> None:
    story = plan.get("selected_story")
    if not isinstance(story, dict):
        raise ValueError("Groq Reel plan must include a selected_story object.")
    title = story.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Groq selected_story must include a non-empty title.")

    for field in (
        "category",
        "headline",
        "image_body",
        "background_image_prompt",
        "description",
    ):
        value = plan.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Groq Reel plan must include a non-empty {field} string.")
    hashtags = plan.get("hashtags", [])
    if not isinstance(hashtags, list) or any(not isinstance(item, str) for item in hashtags):
        raise ValueError("Groq Reel plan hashtags must be a list of strings.")


def _make_ai_client() -> CloudAIResearcher:
    api_keys = getattr(settings, "groq_api_keys", (settings.groq_api_key,))
    return CloudAIResearcher(
        settings.ai_provider,
        api_keys,
        settings.groq_model,
        plan_validator=_validate_research_result,
        credential_state_path=CREDENTIAL_ROTATION_PATH,
    )


def _generate_background(
    prompt: str,
    output_path: Path,
    credentials: RotatingCredentialPool[tuple[str, str]],
    model: str,
) -> Path:
    account_id, api_token = credentials.next_credential()
    try:
        return CloudflareImageGenerator(account_id, api_token, model).generate(
            prompt, output_path
        )
    except AIProviderError as exc:
        raise AIProviderError(
            "Cloudflare image generation failed with the selected round-robin credential: "
            f"{exc}"
        ) from exc


def _prepare_description(plan: dict) -> str:
    description = plan["description"].strip()
    hashtags = plan.get("hashtags", [])
    if hashtags:
        description = f"{description.rstrip()}\n\n{' '.join(hashtags)}"
    return description


def _upload_reel_if_configured(
    reel_path: Path,
    post_dir: Path,
    cloudinary_settings: tuple[str, str, str] | None,
) -> str | None:
    if cloudinary_settings is None:
        return None
    log.info("[4/4] Uploading Reel video to Cloudinary...")
    media = CloudinaryPublicMedia(*cloudinary_settings)
    public_id = (post_dir.relative_to(settings.output_dir) / reel_path.stem).as_posix()
    return media.upload_video(reel_path, public_id)


def run_pipeline(
    text_only: bool = False,
    require_publish_configuration: bool = False,
) -> Path:
    started_at = datetime.now(IST)
    run_id = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = settings.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    event_name = os.getenv("GITHUB_EVENT_NAME", "local")
    log.info(
        "[SCHEDULE] Execution started: %s IST (%s)",
        started_at.strftime("%Y-%m-%d %H:%M:%S"),
        event_name,
    )

    log.info("[NEWS] Fetching current candidates with Groq browser_search.")
    previous_summaries = _read_shortlisted_summaries(SHORTLISTED_HEADLINES_PATH)
    last_posted_at = _read_last_posted_at(POSTING_HISTORY_PATH)
    posting_cooldown_active = (
        last_posted_at is not None
        and started_at.astimezone(timezone.utc) - last_posted_at < POSTING_COOLDOWN
    )
    result = _make_ai_client().research_and_generate_plan(
        build_news_and_post_plan_prompt(
            previous_summaries,
            posting_cooldown_active=posting_cooldown_active,
        ),
        3500,
        run_dir / "groq_response.txt",
    )
    if not isinstance(result, dict):
        raise ValueError("Groq response must be a structured news-research JSON object.")
    _validate_research_result(result)
    metrics = result["research_metrics"]
    assessment = result["publish_assessment"]
    log.info(
        "[NEWS] Collected %d candidates, %d unique stories.",
        metrics["candidate_count"],
        metrics["unique_candidate_count"],
    )
    log.info("[GROQ] Research and ranking completed.")
    story = result.get("selected_story")
    if story is not None:
        log.info("[SELECTION] Selected: %s", story["title"])
    else:
        log.info("[SELECTION] No candidate was eligible for ranking.")

    log.info("[PUBLISH GATE] Cooldown exception: %s", assessment["cooldown_exception"])
    rejection_reason = _publish_gate_rejection_reason(result)
    cooldown_rejection_reason = _posting_cooldown_rejection_reason(
        assessment,
        last_posted_at,
    )
    cooldown_blocked = cooldown_rejection_reason is not None and story is not None
    if cooldown_blocked:
        rejection_reason = " ".join(
            reason
            for reason in (rejection_reason, cooldown_rejection_reason)
            if reason
        )
    if rejection_reason:
        status = "skipped_posting_cooldown" if cooldown_blocked else "skipped_publish_gate"
        log.info("[PUBLISH GATE] Decision: REJECTED")
        log.info("[PUBLISH GATE] Reason: %s", rejection_reason)
        log.info("[PIPELINE] Skipping downstream processing. Execution completed successfully.")
        _write_json(
            run_dir / "run_summary.json",
            {
                "status": status,
                "run_id": run_id,
                "execution_started_at_ist": started_at.isoformat(),
                "ai_provider": "groq",
                "ai_model": settings.groq_model,
                "selection_method": "groq_browser_search",
                "research_metrics": metrics,
                "selected_story": story["title"] if story else None,
                "publish_assessment": assessment,
                "publish_decision": "REJECTED",
                "skip_reason": rejection_reason,
                "background_images_generated": 0,
                "instagram_publishing": "NOT_PUBLISHED",
            },
        )
        return run_dir

    log.info("[PUBLISH GATE] Decision: APPROVED")
    log.info("[PUBLISH GATE] Reason: %s", assessment["reason"])
    plan = result
    _validate_generated_plan_structure(plan)

    cloudinary_settings = None
    if not text_only:
        cloudinary_settings = getattr(settings, "cloudinary_settings", None)
    if require_publish_configuration:
        missing = [
            name
            for name in ("instagram_user_id", "instagram_access_token", "instagram_graph_api_version")
            if not getattr(settings, name, "")
        ]
        if missing:
            raise ValueError(
                "Instagram publishing requires these settings: "
                + ", ".join(name.upper() for name in missing)
            )
        if cloudinary_settings is None:
            raise ValueError(
                "A full Instagram publishing run requires CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
            )

    summary = _get_event_summary(story)
    headline = plan["headline"].strip()
    description = _prepare_description(plan)
    plan["description"] = description
    _write_json(run_dir / "post_plan.json", plan)
    post_dir = run_dir / f"best_news_{_safe_name(headline)}"
    background_dir = post_dir / "backgrounds"
    post_dir.mkdir(parents=True, exist_ok=True)
    background_dir.mkdir(parents=True, exist_ok=True)
    _write_json(post_dir / "post_plan.json", plan)
    _write_json(post_dir / "selected_story.json", story)
    (post_dir / "caption.txt").write_text(description, encoding="utf-8")

    public_video_url: str | None = None
    generated = 0
    image_path = post_dir / "news_image.jpg"
    reel_path = post_dir / "reel.mp4"
    bgm_path: Path | None = None
    if not text_only:
        log.info("[3/4] Cloudflare generating the story background...")
        cloudflare_credentials = getattr(
            settings,
            "cloudflare_credentials",
            ((settings.cloudflare_account_id, settings.cloudflare_api_token),),
        )
        credential_pool = RotatingCredentialPool(
            cloudflare_credentials,
            "Cloudflare",
            CREDENTIAL_ROTATION_PATH,
        )
        background_path = background_dir / "background.jpg"
        _generate_background(
            plan["background_image_prompt"].strip(),
            background_path,
            credential_pool,
            settings.cloudflare_model,
        )
        compose_news_image(
            background_path,
            image_path,
            headline,
            plan["image_body"].strip(),
            settings.brand_name,
            REEL_WIDTH,
            REEL_HEIGHT,
        )
        bgm_path = select_bgm_track(plan["category"])
        create_static_reel(image_path, reel_path, bgm_path)
        generated = 1
        public_video_url = _upload_reel_if_configured(
            reel_path,
            post_dir,
            cloudinary_settings,
        )
        if public_video_url:
            plan["public_video_url"] = public_video_url
            _write_json(post_dir / "post_plan.json", plan)
    else:
        log.info("[3/4] Text-only mode: skipping image, Reel, and video hosting.")

    if not text_only:
        _record_shortlisted_summary(
            SHORTLISTED_HEADLINES_PATH,
            summary.strip(),
            previous_summaries,
        )

    _write_json(
        run_dir / "run_summary.json",
        {
            "status": "completed_text_only" if text_only else "completed",
            "run_id": run_id,
            "execution_started_at_ist": started_at.isoformat(),
            "ai_provider": "groq",
            "ai_model": settings.groq_model,
            "selected_count": 1,
            "selection_method": "groq_browser_search",
            "research_metrics": metrics,
            "selected_story": story["title"],
            "category": plan["category"],
            "publish_assessment": assessment,
            "publish_decision": "APPROVED",
            "bgm_file": str(bgm_path) if bgm_path else None,
            "public_video_url": public_video_url,
            "background_images_generated": generated,
            "post_directory": str(post_dir),
            "reel_path": str(reel_path) if not text_only else None,
            "instagram_publishing": "NOT_PUBLISHED",
        },
    )
    log.info("[4/4] Pipeline finished. Output: %s", run_dir)
    return run_dir
