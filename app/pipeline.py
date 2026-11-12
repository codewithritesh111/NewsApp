import json
import logging
import re
from datetime import datetime
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
from app.intelligence.news_researcher import NEWS_CATEGORIES, build_news_and_post_plan_prompt
from app.social.cloudinary_storage import CloudinaryPublicMedia

log = logging.getLogger(__name__)
SHORTLISTED_HEADLINES_PATH = Path(__file__).resolve().parents[1] / "shortlisted_headlines.json"
MAX_SHORTLISTED_HEADLINES = 15
INSTAGRAM_CAPTION_LIMIT = 2200
ENGAGEMENT_CTA = "Like, share and follow"
HASHTAG_MIN_COUNT = 5
HASHTAG_MAX_COUNT = 15
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


def _validate_no_recent_story_overlap(plan: dict, previous_summaries: list[str]) -> None:
    story = plan["selected_story"]
    current_keywords = _story_keywords(
        f"{story['title']} {_get_event_summary(story)}"
    )
    if len(current_keywords) < 2:
        return
    for previous_summary in previous_summaries:
        previous_keywords = _story_keywords(previous_summary)
        if len(previous_keywords) < 2:
            continue
        shared_keywords = current_keywords & previous_keywords
        overlap_ratio = len(shared_keywords) / min(len(current_keywords), len(previous_keywords))
        if len(shared_keywords) >= 2 and overlap_ratio >= 0.5:
            raise ValueError(
                "Groq selected a story with substantially overlapping main keywords "
                f"from shortlisted history: {previous_summary!r}."
            )


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
        plan_validator=lambda plan: _validate_generated_plan(plan, previous_summaries),
    )


def _generate_background(
    prompt: str,
    output_path: Path,
    credentials: RotatingCredentialPool[tuple[str, str]],
    model: str,
) -> Path:
    last_error: AIProviderError | None = None
    credential_count = credentials.credential_count
    for attempt, (account_id, api_token) in enumerate(credentials.attempts(), start=1):
        try:
            return CloudflareImageGenerator(account_id, api_token, model).generate(
                prompt, output_path
            )
        except AIProviderError as exc:
            last_error = exc
            if attempt < credential_count:
                log.warning(
                    "Cloudflare image request failed with credential %d/%d; "
                    "trying the next configured credential.",
                    attempt,
                    credential_count,
                )

    raise AIProviderError(
        "Cloudflare image generation failed with all "
        f"{credential_count} configured credential(s). Last error: {last_error}"
    ) from last_error


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


def run_pipeline(text_only: bool = False) -> Path:
    run_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = settings.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cloudinary_settings = (
        getattr(settings, "cloudinary_settings", None)
        if not text_only
        else None
    )

    log.info("[1/4] Groq searching current India news and creating one image Reel plan...")
    previous_summaries = _read_shortlisted_summaries(SHORTLISTED_HEADLINES_PATH)
    plan = _make_ai_client(previous_summaries).research_and_generate_plan(
        build_news_and_post_plan_prompt(previous_summaries),
        9000,
        run_dir / "groq_response.txt",
    )
    if not isinstance(plan, dict):
        raise ValueError("Groq response must be a Reel-plan JSON object.")
    _validate_generated_plan(plan)
    story = plan["selected_story"]
    log.info("[2/4] Selected: %s", story["title"])

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
        credential_pool = RotatingCredentialPool(cloudflare_credentials, "Cloudflare")
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
            "ai_provider": "groq",
            "ai_model": settings.groq_model,
            "selected_count": 1,
            "selection_method": "groq_browser_search",
            "selected_story": story["title"],
            "category": plan["category"],
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
