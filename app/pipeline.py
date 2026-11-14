import json
import logging
import os
import re
from difflib import SequenceMatcher
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
    NEWS_CATEGORIES,
    build_news_and_post_plan_prompt,
)
from app.social.cloudinary_storage import CloudinaryPublicMedia

log = logging.getLogger(__name__)
SHORTLISTED_HEADLINES_PATH = Path(__file__).resolve().parents[1] / "shortlisted_headlines.json"
POSTING_HISTORY_PATH = Path(__file__).resolve().parents[1] / "posting_history.json"
MAX_SHORTLISTED_HEADLINES = 15
POSTING_COOLDOWN = timedelta(hours=3)
CREDENTIAL_ROTATION_PATH = Path(__file__).resolve().parents[1] / "credential_rotation.json"
INSTAGRAM_CAPTION_LIMIT = 2200
ENGAGEMENT_CTA = "Like, share and follow"
HASHTAG_MIN_COUNT = 5
HASHTAG_MAX_COUNT = 15
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
GENERIC_STORY_WORDS = {
    "about",
    "after",
    "against",
    "amid",
    "announced",
    "announces",
    "around",
    "before",
    "being",
    "break",
    "breaking",
    "country",
    "development",
    "during",
    "first",
    "from",
    "government",
    "india",
    "indian",
    "into",
    "latest",
    "major",
    "more",
    "news",
    "official",
    "officials",
    "over",
    "report",
    "reports",
    "said",
    "says",
    "story",
    "that",
    "their",
    "there",
    "these",
    "this",
    "today",
    "update",
    "updates",
    "were",
    "which",
    "with",
}


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


def _summary_word_count(summary: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", summary, flags=re.UNICODE))


def _get_event_summary(story: dict) -> str:
    summary = story.get("event_summary")
    if isinstance(summary, str) and 5 <= _summary_word_count(summary) <= 15:
        return summary.strip()

    for field in ("title", "trend_rationale"):
        fallback = story.get(field)
        if not isinstance(fallback, str) or not fallback.strip():
            continue
        words = re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", fallback, flags=re.UNICODE)
        if len(words) >= 5:
            log.warning(
                "Groq event_summary was missing or outside 5-15 words; using selected_story.%s.",
                field,
            )
            return " ".join(words[:15])

    raise ValueError(
        "Groq selected_story must include an event_summary of 5 to 15 words "
        "or a usable title or trend_rationale."
    )


def _story_keywords(text: str) -> set[str]:
    return {
        word.casefold()
        for word in re.findall(r"[^\W_]+", text, flags=re.UNICODE)
        if len(word) >= 3 and word.casefold() not in GENERIC_STORY_WORDS
    }


def _find_recent_story_overlap(story: dict, previous_summaries: list[str]) -> str | None:
    current_texts = [
        value.strip()
        for value in (story.get("title", ""), story.get("event_summary", ""))
        if isinstance(value, str) and value.strip()
    ]
    for previous_summary in previous_summaries:
        previous_keywords = _story_keywords(previous_summary)
        if len(previous_keywords) < 2:
            continue
        for current_text in current_texts:
            current_keywords = _story_keywords(current_text)
            if len(current_keywords) < 2:
                continue
            shared_keywords = current_keywords & previous_keywords
            overlap_ratio = len(shared_keywords) / min(len(current_keywords), len(previous_keywords))
            text_similarity = SequenceMatcher(
                None,
                " ".join(sorted(current_keywords)),
                " ".join(sorted(previous_keywords)),
            ).ratio()
            if (
                (len(shared_keywords) >= 2 and overlap_ratio >= 0.25)
                or text_similarity >= 0.72
            ):
                return previous_summary
    return None


def _validate_no_recent_story_overlap(
    plan: dict,
    previous_summaries: list[str],
) -> None:
    overlap = _find_recent_story_overlap(plan["selected_story"], previous_summaries)
    if overlap:
        raise ValueError(
            "Groq selected a story that overlaps shortlisted news "
            f"from shortlisted history: {overlap!r}."
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
    previous_summaries: list[str] | None = None,
) -> None:
    metrics = result.get("research_metrics")
    if not isinstance(metrics, dict):
        raise ValueError("Groq research result must include research_metrics.")
    candidate_count = metrics.get("candidate_count")
    unique_count = metrics.get("unique_candidate_count")
    if (
        type(candidate_count) is not int
        or type(unique_count) is not int
        or not 0 <= candidate_count <= 5
        or not 0 <= unique_count <= candidate_count
    ):
        raise ValueError("Groq research metrics must contain valid candidate counts.")

    assessment = result.get("publish_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("Groq research result must include a structured publish_assessment.")
    if type(assessment.get("eligible")) is not bool:
        raise ValueError("Publish assessment eligible must be a boolean.")
    if assessment.get("cooldown_exception") not in COOLDOWN_EXCEPTIONS:
        raise ValueError("Publish assessment cooldown_exception must use a supported value.")
    for field in ("reason", "cooldown_exception_evidence"):
        if not isinstance(assessment.get(field), str):
            raise ValueError(f"Publish assessment {field} must be a string.")
    if (
        assessment["cooldown_exception"] != "NONE"
        and not assessment["cooldown_exception_evidence"].strip()
    ):
        raise ValueError("A cooldown exception must include supporting evidence.")

    story = result.get("selected_story")
    if story is None:
        if assessment["eligible"]:
            raise ValueError("Groq cannot approve publishing when no story was selected.")
        return
    if not isinstance(story, dict) or not isinstance(story.get("title"), str) or not story["title"].strip():
        raise ValueError("Groq selected_story must include a non-empty title or be null.")
    if unique_count == 0:
        raise ValueError("Groq cannot select a story when no unique candidate was found.")
    if assessment["eligible"]:
        _validate_generated_plan(result, previous_summaries)


def _publish_gate_rejection_reason(
    result: dict,
    previous_summaries: list[str],
) -> str | None:
    assessment = result["publish_assessment"]
    if not assessment["eligible"]:
        reason = assessment["reason"].strip() or "Groq rejected the story for publishing."
    else:
        reason = ""

    story = result.get("selected_story")
    if story is None:
        reason = "No credible, non-duplicate news candidate was selected."
    else:
        overlap = _find_recent_story_overlap(story, previous_summaries)
        if overlap:
            reason = "The story overlaps shortlisted news and must not be selected again."
    return reason or None


def _validate_generated_plan(
    plan: dict,
    previous_summaries: list[str] | None = None,
) -> None:
    story = plan.get("selected_story")
    if not isinstance(story, dict):
        raise ValueError("Groq Reel plan must include a selected_story object.")
    title = story.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Groq selected_story must include a non-empty title.")
    _get_event_summary(story)

    category = plan.get("category")
    if category not in NEWS_CATEGORIES:
        raise ValueError("Groq Reel plan must use one of the supported news categories.")
    for field in ("headline", "image_body", "background_image_prompt", "description"):
        value = plan.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Groq Reel plan must include a non-empty {field}.")
    image_body_words = _summary_word_count(plan["image_body"])
    if not 25 <= image_body_words <= 45:
        raise ValueError("Groq image_body must contain 25 to 45 words for a self-contained Reel.")
    description = plan["description"].strip()
    if _summary_word_count(description) > 500:
        raise ValueError("Groq description must not exceed 500 words.")
    if len(description) > INSTAGRAM_CAPTION_LIMIT:
        raise ValueError(
            f"Groq description must not exceed Instagram's {INSTAGRAM_CAPTION_LIMIT}-character limit."
        )
    if description.casefold().count(ENGAGEMENT_CTA.casefold()) > 1:
        raise ValueError('"Like, share and follow" must appear only once in the description.')
    hashtags = plan.get("hashtags")
    if not isinstance(hashtags, list) or not HASHTAG_MIN_COUNT <= len(hashtags) <= HASHTAG_MAX_COUNT:
        raise ValueError("Groq Reel plan must include 5 to 15 relevant hashtags.")
    if any(
        not isinstance(hashtag, str)
        or not re.fullmatch(r"#[^\s#]+", hashtag)
        for hashtag in hashtags
    ):
        raise ValueError("Every Groq hashtag must be a single valid hashtag starting with #.")
    if len({hashtag.casefold() for hashtag in hashtags}) != len(hashtags):
        raise ValueError("Groq hashtags must be unique.")
    prepared_caption = _prepare_description(plan)
    if len(prepared_caption) > INSTAGRAM_CAPTION_LIMIT:
        raise ValueError(
            f"Groq description and hashtags together must fit Instagram's "
            f"{INSTAGRAM_CAPTION_LIMIT}-character caption limit."
        )
    sources = plan.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Groq Reel plan must include at least one source.")
    _validate_no_recent_story_overlap(plan, previous_summaries or [])


def _make_ai_client(previous_summaries: list[str] | None = None) -> CloudAIResearcher:
    api_keys = getattr(settings, "groq_api_keys", (settings.groq_api_key,))
    return CloudAIResearcher(
        settings.ai_provider,
        api_keys,
        settings.groq_model,
        plan_validator=lambda plan: _validate_research_result(plan, previous_summaries),
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
    if ENGAGEMENT_CTA.casefold() not in description.casefold():
        description = f"{description.rstrip()}\n\n{ENGAGEMENT_CTA}"
    hashtags = plan.get("hashtags", [])
    if hashtags:
        description = f"{description.rstrip()}\n\n{' '.join(hashtags)}"
    if len(description) > INSTAGRAM_CAPTION_LIMIT:
        raise ValueError(
            f"Instagram description exceeds the {INSTAGRAM_CAPTION_LIMIT}-character limit "
            "after adding the engagement call to action."
        )
    if _summary_word_count(description) > 500:
        raise ValueError("Instagram description exceeds the 500-word limit.")
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
    result = _make_ai_client(previous_summaries).research_and_generate_plan(
        build_news_and_post_plan_prompt(
            previous_summaries,
            posting_cooldown_active=posting_cooldown_active,
        ),
        3500,
        run_dir / "groq_response.txt",
    )
    if not isinstance(result, dict):
        raise ValueError("Groq response must be a structured news-research JSON object.")
    _validate_research_result(result, previous_summaries)
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
    rejection_reason = _publish_gate_rejection_reason(result, previous_summaries)
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
    _validate_generated_plan(plan, previous_summaries)

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
