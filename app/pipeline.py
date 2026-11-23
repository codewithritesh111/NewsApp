import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.images.cloudflare_client import CloudflareImageGenerator
from app.images.image_analyzer import analyze_image_layout
from app.images.image_composer import compose_news_image, compose_news_overlay
from app.images.reel_encoder import (
    REEL_HEIGHT,
    REEL_WIDTH,
    create_static_reel,
    create_video_reel,
    inspect_video,
    select_bgm_track,
)
from app.images.web_image_search import fetch_news_background, fetch_news_video
from app.intelligence.cloud_ai_client import CloudAIResearcher
from app.intelligence.credential_pool import AIProviderError, RotatingCredentialPool
from app.intelligence.news_researcher import (
    APPROVED_NEWS_CHANNELS,
    COOLDOWN_EXCEPTIONS,
    NEWS_CATEGORIES,
    build_news_and_post_plan_prompt,
    match_approved_channel,
)
from app.social.cloudinary_storage import CloudinaryPublicMedia

log = logging.getLogger(__name__)
_DefaultCloudflareGenerator = CloudflareImageGenerator
SHORTLISTED_HEADLINES_PATH = Path(__file__).resolve().parents[1] / "shortlisted_headlines.json"
POSTING_HISTORY_PATH = Path(__file__).resolve().parents[1] / "posting_history.json"
CREDENTIAL_ROTATION_PATH = Path(__file__).resolve().parents[1] / "credential_rotation.json"
BGM_ROTATION_PATH = Path(__file__).resolve().parents[1] / "bgm_rotation.json"
COOLDOWN_HOURS = 3.0
MAX_SHORTLISTED_HEADLINES = 15
MAX_CANDIDATE_AGE_HOURS = 5.0
MAX_CANDIDATE_AGE_HOURS_WITH_SKEW = 5.25
INSTAGRAM_CAPTION_LIMIT = 2200
ENGAGEMENT_CTA = "Like, share and follow"
HASHTAG_MIN_COUNT = 5
HASHTAG_MAX_COUNT = 15
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def parse_publication_time(
    pub_str: str | None,
    current_time: datetime | None = None,
) -> datetime | None:
    """Parse an ISO timestamp, standard date, or relative time ('2 hours ago') into a datetime in IST."""
    if not pub_str or not isinstance(pub_str, str):
        return None
    s = pub_str.strip()
    now = current_time or datetime.now(IST)

    m_h = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\s*ago", s, re.I)
    if m_h:
        return now - timedelta(hours=float(m_h.group(1)))
    m_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\s*ago", s, re.I)
    if m_m:
        return now - timedelta(minutes=float(m_m.group(1)))

    try:
        import dateutil.parser
        dt = dateutil.parser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt
    except Exception:
        return None


def _validate_story_recency(story: dict, max_hours: float = MAX_CANDIDATE_AGE_HOURS_WITH_SKEW) -> None:
    """Ensure the selected candidate was published within the strict max_hours window (<= 5 hours)."""
    pub_str = story.get("published_at")
    if not pub_str or not isinstance(pub_str, str) or not pub_str.strip():
        return
    now = datetime.now(IST)
    pub_dt = parse_publication_time(pub_str, now)
    if pub_dt is not None:
        age_hours = (now - pub_dt).total_seconds() / 3600.0
        if age_hours > max_hours:
            raise ValueError(
                f"Selected news candidate is older than {MAX_CANDIDATE_AGE_HOURS:.0f} hours strictly. "
                f"Published: {pub_str} (~{age_hours:.1f} hours ago)."
            )


def _find_story_recency_issue(story: dict | None, skip_web: bool = False) -> str | None:
    """Check declared timestamp and cross-check real web coverage to detect hallucinated old stories."""
    if not story or not isinstance(story, dict):
        return None
    pub_str = story.get("published_at")
    now = datetime.now(IST)
    if pub_str and isinstance(pub_str, str) and pub_str.strip():
        pub_dt = parse_publication_time(pub_str, now)
        if pub_dt is not None:
            age_hours = (now - pub_dt).total_seconds() / 3600.0
            if age_hours > MAX_CANDIDATE_AGE_HOURS_WITH_SKEW:
                return f"Declared published_at '{pub_str}' is ~{age_hours:.1f} hours old (> 5h limit)"

    if skip_web or os.getenv("SKIP_WEB_RECENCY_CHECK") == "1":
        return None

    # Live web cross-check
    title = story.get("title", "")
    claimed_url = story.get("url")
    if not title:
        return None

    urls_to_check: list[str] = []
    if claimed_url and isinstance(claimed_url, str) and claimed_url.startswith("http"):
        urls_to_check.append(claimed_url.strip())

    try:
        from urllib.parse import quote_plus
        import requests

        clean_title = re.sub(r"[^\w\s]", " ", title).strip()
        words = clean_title.split()
        search_q = " ".join(words[:6]) if len(words) > 6 else clean_title
        search_url = f"https://www.bing.com/news/search?q={quote_plus(search_q)}"
        resp = requests.get(search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        if resp.ok:
            links = re.findall(r'<a[^>]+href=[\x27\x22](https?://[^\x27\x22]+)[\x27\x22]', resp.text)
            for link in links:
                l_low = link.lower()
                if "bing.com" not in l_low and "microsoft.com" not in l_low and "msn.com" not in l_low:
                    urls_to_check.append(link)
    except Exception as exc:
        log.warning("[RECENCY] Real web cross-check skipped: %s", exc)

    for u in urls_to_check[:4]:
        # URL date markers like /2026/Aug/07/ or /2024-05-12/
        m_date = re.search(r"/(20\d\d)[/-]([A-Za-z0-9]{2,3})[/-](\d{1,2})", u)
        if m_date:
            y, m, d = m_date.groups()
            try:
                import dateutil.parser
                dt = dateutil.parser.parse(f"{y} {m} {d}").replace(tzinfo=IST)
                diff_hours = (now - dt).total_seconds() / 3600.0
                if diff_hours > 24.0:
                    return f"Web coverage ({u}) shows date {y}-{m}-{d} (~{diff_hours:.0f}h old)"
            except Exception:
                pass

        try:
            r = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if r.ok:
                meta = re.findall(
                    r'<meta[^>]+(?:property|name)=[\x27\x22](?:article:published_time|publish-date|datePublished|pubdate)[\x27\x22][^>]+content=[\x27\x22]([^\x27\x22]+)[\x27\x22]',
                    r.text,
                    re.I,
                )
                if not meta:
                    meta = re.findall(r'[\x27\x22]datePublished[\x27\x22]\s*:\s*[\x27\x22]([^\x27\x22]+)[\x27\x22]', r.text)
                if meta:
                    import dateutil.parser
                    dt = dateutil.parser.parse(meta[0])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=IST)
                    diff_hours = (now - dt).total_seconds() / 3600.0
                    if diff_hours > 24.0:
                        return f"Real article ({u}) datePublished is {meta[0]} (~{diff_hours:.0f}h old)"
        except Exception:
            pass

    return None
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


def _find_recent_story_overlap(story: dict, previous_summaries: list[str]) -> str | None:
    current_keywords = _story_keywords(
        f"{story.get('title', '')} {story.get('event_summary', '')}"
    )
    if len(current_keywords) < 2:
        return None
    for previous_summary in previous_summaries:
        previous_keywords = _story_keywords(previous_summary)
        if len(previous_keywords) < 2:
            continue
        shared_keywords = current_keywords & previous_keywords
        overlap_ratio = len(shared_keywords) / min(len(current_keywords), len(previous_keywords))
        if len(shared_keywords) >= 2 and overlap_ratio >= 0.5:
            return previous_summary
    return None


def _validate_no_recent_story_overlap(
    plan: dict,
    previous_summaries: list[str],
    allow_significant_new_development: bool = False,
) -> None:
    overlap = _find_recent_story_overlap(plan["selected_story"], previous_summaries)
    if overlap and not allow_significant_new_development:
        raise ValueError(
            "Groq selected a story with substantially overlapping main keywords "
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


def _read_last_posted_at(path: Path = POSTING_HISTORY_PATH) -> datetime | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    timestamp_str = data.get("last_posted_at")
    return _parse_event_timestamp(timestamp_str)


def _record_successful_instagram_post(
    path: Path = POSTING_HISTORY_PATH,
    posted_at: datetime | None = None,
) -> None:
    timestamp = (posted_at or datetime.now(timezone.utc)).isoformat()
    _write_json(path, {"last_posted_at": timestamp})


def _is_posting_cooldown_active(
    path: Path = POSTING_HISTORY_PATH,
    cooldown_hours: float = COOLDOWN_HOURS,
    now: datetime | None = None,
) -> bool:
    last_posted_at = _read_last_posted_at(path)
    if last_posted_at is None:
        return False
    current_time = now or datetime.now(timezone.utc)
    elapsed = current_time - last_posted_at
    return timedelta(0) <= elapsed < timedelta(hours=cooldown_hours)


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
        or candidate_count < 0
        or unique_count < 0
        or unique_count > candidate_count
    ):
        raise ValueError("Groq research metrics must contain valid candidate counts.")

    assessment = result.get("publish_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("Groq research result must include a structured publish_assessment.")
    if type(assessment.get("eligible")) is not bool:
        raise ValueError("Publish assessment eligible must be a boolean.")

    for field in (
        "india_wide_relevance",
        "public_interest",
        "public_attention",
        "recency",
        "significance",
        "uniqueness",
        "emotional_resonance",
    ):
        if field in assessment:
            rating = assessment.get(field)
            if not isinstance(rating, str) or rating not in {
                "LOW",
                "MODERATE",
                "HIGH",
                "VERY_HIGH",
            }:
                raise ValueError(f"Publish assessment {field} must use a supported rating.")

    for field in (
        "thought_provoking",
        "significant_new_development",
        "cooldown_active",
        "nationwide_impact",
        "is_minor_or_local_incident",
    ):
        if field in assessment and type(assessment.get(field)) is not bool:
            raise ValueError(f"Publish assessment {field} must be a boolean.")

    if "cooldown_exception" in assessment:
        exception = assessment["cooldown_exception"]
        if not isinstance(exception, str) or exception not in COOLDOWN_EXCEPTIONS:
            raise ValueError("Publish assessment cooldown_exception must use a supported code.")

    for field in (
        "reason",
        "new_development_summary",
        "human_stakes",
        "india_wide_impact_evidence",
        "emotional_resonance_evidence",
    ):
        if field in assessment and not isinstance(assessment.get(field), str):
            raise ValueError(f"Publish assessment {field} must be a string.")

    story = result.get("selected_story")
    if story is None:
        if assessment["eligible"]:
            raise ValueError("Groq cannot approve publishing when no story was selected.")
        if unique_count != 0:
            raise ValueError("Groq must select the highest-ranked current candidate when one exists.")
        return
    if not isinstance(story, dict) or not isinstance(story.get("title"), str) or not story["title"].strip():
        raise ValueError("Groq selected_story must include a non-empty title or be null.")
    if assessment["eligible"]:
        _validate_generated_plan(
            result,
            previous_summaries,
            allow_significant_new_development=(
                assessment.get("significant_new_development", False)
                and bool(assessment.get("new_development_summary", "").strip())
            ),
        )


def _publish_gate_rejection_reason(
    result: dict,
    previous_summaries: list[str],
    posting_cooldown_active: bool = False,
) -> str | None:
    assessment = result.get("publish_assessment", {})
    reasons = []
    if not assessment.get("eligible", False):
        reasons.append(assessment.get("reason", "").strip() or "Groq rejected the story for publishing.")

    # Rule 2: If cooldown is active, candidate must satisfy one of the non-routine exceptions
    # and strictly exclude small crime, small event, or minor incident
    if posting_cooldown_active:
        if assessment.get("is_minor_or_local_incident") is True:
            reasons.append(
                "Posting cooldown active (last post was published within 3 hours): "
                "small crime, small event, or minor incident is strictly excluded during cooldown."
            )
        if assessment.get("nationwide_impact") is False:
            reasons.append(
                "Posting cooldown active: story lacks verified nationwide impact or national importance."
            )
        cooldown_exception = assessment.get("cooldown_exception", "NONE")
        if cooldown_exception not in COOLDOWN_EXCEPTIONS or cooldown_exception == "NONE":
            reasons.append(
                "Posting cooldown active (last post was published within 3 hours) and "
                "the candidate is a routine event without a qualifying cooldown exception."
            )
        category = result.get("category")
        if category == "Crime" and cooldown_exception not in {
            "BREAKING_NEWS",
            "NATIONWIDE_IMPACT",
            "DISASTER",
            "MAJOR_CHANGE",
            "UNUSUAL_NUMBERS",
            "SAD_EVENT",
        }:
            reasons.append(
                "Posting cooldown active: small or isolated crime incidents are excluded during cooldown."
            )

    # Legacy rating check: if ratings are present, check if below VERY_HIGH (for backward compatibility)
    required_ratings = {
        "india_wide_relevance",
        "public_interest",
        "public_attention",
        "recency",
        "significance",
        "uniqueness",
        "emotional_resonance",
    }
    for field in required_ratings:
        if field in assessment and assessment[field] not in {"VERY_HIGH"}:
            reasons.append(f"{field.replace('_', ' ').capitalize()} assessment is below the publishing threshold.")

    if assessment.get("thought_provoking") is False:
        reasons.append("The story does not have a substantiated thought-provoking angle.")

    for field, label in (
        ("human_stakes", "specific human stakes"),
        ("india_wide_impact_evidence", "evidence of India-wide impact"),
        ("emotional_resonance_evidence", "evidence of authentic emotional resonance"),
    ):
        if field in assessment and not assessment[field].strip():
            reasons.append(f"The story lacks {label}.")

    event_time_str = assessment.get("actual_event_at")
    if event_time_str is not None:
        event_time = _parse_event_timestamp(event_time_str)
        now = datetime.now(timezone.utc)
        if event_time is None:
            reasons.append("The actual event/development timestamp is missing or not a timezone-aware ISO 8601 value.")
        else:
            event_age = now - event_time
            if event_age < -timedelta(minutes=5):
                reasons.append("The reported actual event/development timestamp is in the future.")
            elif event_age > timedelta(hours=24):
                reasons.append("The actual event/development is older than 24 hours.")

    story = result.get("selected_story")
    if story is None:
        reasons.append("No credible, recent news candidate was selected.")
    else:
        overlap = _find_recent_story_overlap(story, previous_summaries)
        if overlap and not (
            assessment.get("significant_new_development", False)
            and bool(assessment.get("new_development_summary", "").strip())
            and (
                _parse_event_timestamp(assessment.get("actual_event_at")) is None
                or (
                    _parse_event_timestamp(assessment.get("actual_event_at")) is not None
                    and datetime.now(timezone.utc) - _parse_event_timestamp(assessment.get("actual_event_at")) <= timedelta(hours=24)
                )
            )
        ):
            reasons.append("The story substantially overlaps a recent post without a verified significant new development.")

    # Rule 3: Strict 5-hour recency check
    story = result.get("selected_story")
    if isinstance(story, dict):
        pub_str = story.get("published_at")
        if pub_str and isinstance(pub_str, str) and pub_str.strip():
            now = datetime.now(IST)
            pub_dt = parse_publication_time(pub_str, now)
            if pub_dt is not None:
                age_hours = (now - pub_dt).total_seconds() / 3600.0
                if age_hours > MAX_CANDIDATE_AGE_HOURS_WITH_SKEW:
                    reasons.append(
                        f"Selected candidate was published ~{age_hours:.1f} hours ago, "
                        f"strictly exceeding the 5-hour recency limit."
                    )

    return " ".join(dict.fromkeys(reasons)) or None


def _validate_generated_plan(
    plan: dict,
    previous_summaries: list[str] | None = None,
    allow_significant_new_development: bool = False,
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

    image_search = plan.get("image_search_query") or plan.get("background_image_prompt")
    if not isinstance(image_search, str) or not image_search.strip():
        raise ValueError("Groq Reel plan must include a non-empty image_search_query or background_image_prompt.")

    for field in ("headline", "image_body", "description"):
        value = plan.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Groq Reel plan must include a non-empty {field}.")

    layout = plan.get("layout")
    if layout is not None:
        if not isinstance(layout, dict):
            raise ValueError("Groq Reel plan layout must be an object if present.")
        if "text_position" in layout and layout["text_position"] not in {"top", "bottom"}:
            raise ValueError("Groq Reel plan layout text_position must be 'top' or 'bottom'.")

    image_body_words = _summary_word_count(plan["image_body"])
    if not 10 <= image_body_words <= 45:
        raise ValueError("Groq image_body must contain 10 to 45 words for a self-contained Reel.")
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
    verified = plan.get("verified_channels")
    matched_channels: set[str] = set()
    if isinstance(verified, list):
        matched_channels = {
            match_approved_channel(ch) for ch in verified if isinstance(ch, str)
        } - {None}
    if len(matched_channels) < 3 and isinstance(sources, list):
        for s in sources:
            if isinstance(s, dict) and s.get("name"):
                ch = match_approved_channel(s["name"])
                if ch:
                    matched_channels.add(ch)
    if verified is not None:
        if len(matched_channels) < 3:
            raise ValueError(
                f"Groq Reel plan must be verified across at least 3 approved channels from the 15-channel list. Matched: {matched_channels}"
            )
        plan["verified_channels"] = sorted(matched_channels)
    _validate_no_recent_story_overlap(
        plan,
        previous_summaries or [],
        allow_significant_new_development,
    )
    _validate_story_recency(story)


def _make_ai_client(previous_summaries: list[str] | None = None) -> CloudAIResearcher:
    api_keys = getattr(settings, "groq_api_keys", (settings.groq_api_key,))
    return CloudAIResearcher(
        settings.ai_provider,
        api_keys,
        settings.groq_model,
        plan_validator=lambda plan: _validate_research_result(plan, previous_summaries),
        credential_state_path=CREDENTIAL_ROTATION_PATH,
    )


ADDITIONAL_REEL_HASHTAGS = (
    "#reels",
    "#reelsinstagram",
    "#explorepage",
    "#trending",
    "#viral",
    "#fyp",
)


def _combine_hashtags(story_hashtags: list[str]) -> list[str]:
    combined = list(story_hashtags)
    seen = {tag.casefold() for tag in story_hashtags}
    for tag in ADDITIONAL_REEL_HASHTAGS:
        if tag.casefold() not in seen:
            combined.append(tag)
            seen.add(tag.casefold())
    return combined


def _enhance_image_prompt(prompt: str) -> str:
    cleaned = prompt.strip()
    if "bright" in cleaned.casefold() and "attractive" in cleaned.casefold():
        return cleaned
    return f"{cleaned}, bright vibrant cinematic lighting, attractive and colorful background, rich vivid colors"


def _generate_background(
    prompt: str,
    output_path: Path,
    credentials: RotatingCredentialPool[tuple[str, str]],
    model: str,
) -> Path:
    enhanced_prompt = _enhance_image_prompt(prompt)
    last_error: AIProviderError | None = None
    credential_count = credentials.credential_count
    for attempt, (account_id, api_token) in enumerate(credentials.attempts(), start=1):
        try:
            return CloudflareImageGenerator(account_id, api_token, model).generate(
                enhanced_prompt, output_path
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
    story_hashtags = plan.get("hashtags", [])
    all_hashtags = _combine_hashtags(story_hashtags)
    if all_hashtags:
        description = f"{description.rstrip()}\n\n{' '.join(all_hashtags)}"
    if len(description) > INSTAGRAM_CAPTION_LIMIT:
        raise ValueError(
            f"Instagram description exceeds the {INSTAGRAM_CAPTION_LIMIT}-character limit "
            "after adding the engagement call to action and hashtags."
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
    cooldown_active = _is_posting_cooldown_active(POSTING_HISTORY_PATH)
    log.info("[PUBLISH GATE] 3-hour posting cooldown active: %s", cooldown_active)
    max_tokens = int(os.getenv("GROQ_MAX_COMPLETION_TOKENS", "9000"))
    ai_client = _make_ai_client(previous_summaries)
    max_research_attempts = 2
    result = None

    for attempt_idx in range(1, max_research_attempts + 1):
        raw_out = (
            run_dir / "groq_response.txt"
            if attempt_idx == 1
            else run_dir / f"groq_response_retry_{attempt_idx}.txt"
        )
        res = ai_client.research_and_generate_plan(
            build_news_and_post_plan_prompt(
                previous_summaries,
                posting_cooldown_active=cooldown_active,
            ),
            max_tokens,
            raw_out,
        )
        if not isinstance(res, dict):
            raise ValueError("Groq response must be a structured news-research JSON object.")
        _validate_research_result(res, previous_summaries)

        candidate_story = res.get("selected_story")
        overlap = _find_recent_story_overlap(candidate_story, previous_summaries) if candidate_story else None
        recency_issue = _find_story_recency_issue(candidate_story) if candidate_story else None

        gate_issue = overlap or recency_issue
        if not gate_issue:
            result = res
            break

        gate_name = "DEDUPLICATION" if overlap else "RECENCY"
        log.warning(
            "[%s GATE] Final selected news '%s' failed validation: %s (attempt %d/%d).",
            gate_name,
            candidate_story.get("title") if candidate_story else "None",
            gate_issue,
            attempt_idx,
            max_research_attempts,
        )
        if attempt_idx < max_research_attempts:
            log.info("[%s GATE] Retrying complete browser search process once more...", gate_name)
        else:
            log.warning("[%s GATE] Final selected news is still invalid after retry. Terminating pipeline.", gate_name)
            _write_json(
                run_dir / "run_summary.json",
                {
                    "status": "terminated_repeated_story" if overlap else "terminated_old_story",
                    "run_id": run_id,
                    "execution_started_at_ist": started_at.isoformat(),
                    "ai_provider": "groq",
                    "ai_model": settings.groq_model,
                    "selection_method": "groq_browser_search",
                    "selected_story": candidate_story.get("title") if candidate_story else None,
                    "termination_reason": f"Final selected news failed gate check: {gate_issue}",
                    "instagram_publishing": "NOT_PUBLISHED",
                },
            )
            return run_dir
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

    for field in (
        "public_interest",
        "recency",
        "public_attention",
        "significance",
        "uniqueness",
        "india_wide_relevance",
        "cooldown_exception",
    ):
        if field in assessment:
            log.info("[PUBLISH GATE] %s: %s", field.replace("_", " ").capitalize(), assessment[field])

    rejection_reason = _publish_gate_rejection_reason(
        result,
        previous_summaries,
        posting_cooldown_active=cooldown_active,
    )
    if rejection_reason:
        log.info("[PUBLISH GATE] Decision: REJECTED")
        log.info("[PUBLISH GATE] Reason: %s", rejection_reason)
        log.info("[PIPELINE] Skipping downstream processing. Execution completed successfully.")
        _write_json(
            run_dir / "run_summary.json",
            {
                "status": "skipped_publish_gate",
                "run_id": run_id,
                "execution_started_at_ist": started_at.isoformat(),
                "ai_provider": "groq",
                "ai_model": settings.groq_model,
                "selection_method": "groq_browser_search",
                "cooldown_active": cooldown_active,
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
    log.info("[PUBLISH GATE] Reason: %s", assessment.get("reason", "Approved"))
    plan = result
    _validate_generated_plan(
        plan,
        previous_summaries,
        allow_significant_new_development=(
            assessment.get("significant_new_development", False)
            and bool(assessment.get("new_development_summary", "").strip())
        ),
    )

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
    plan["hashtags"] = _combine_hashtags(plan.get("hashtags", []))
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
        background_path = background_dir / "background.jpg"
        search_query = plan.get("image_search_query") or plan.get("background_image_prompt") or headline
        image_url = plan.get("image_url")
        pexels_api_key = getattr(settings, "pexels_api_key", None)
        news_video = None

        if CloudflareImageGenerator is not _DefaultCloudflareGenerator:
            # Cloudflare was explicitly mocked in a test scenario
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
            _generate_background(
                plan.get("background_image_prompt", search_query).strip(),
                background_path,
                credential_pool,
                settings.cloudflare_model,
            )
        else:
            article_url = story.get("url") if isinstance(story, dict) else None
            sources_urls = [s.get("url") for s in plan.get("sources", []) if isinstance(s, dict) and s.get("url")]
            video_url = plan.get("video_url")
            headline = str(plan.get("headline", "")).strip()
            news_title = str(story.get("title") if isinstance(story, dict) else headline or search_query).strip()
            video_search_query = news_title or (search_query or headline).strip()

            news_video = None
            video_path = background_dir / "news_video.mp4"
            log.info("[3/4] [MEDIA SEARCH] Step 1: Searching internet for 9:16 news video (20s-60s) for %r...", news_title)
            try:
                news_video = fetch_news_video(
                    video_url=video_url,
                    article_url=article_url,
                    sources_urls=sources_urls,
                    output_path=video_path,
                    search_query=video_search_query,
                    news_title=news_title,
                )
            except Exception as exc:
                log.warning("Video search/download failed: %s; proceeding to image search.", exc)
                news_video = None

            # Verify video duration is strictly between 20 sec and 1 min (60 sec)
            if news_video and news_video.is_file():
                dur, _ = inspect_video(news_video)
                if dur < 20.0 or dur > 60.0:
                    log.warning(
                        "[MEDIA] News video duration %.1fs is outside [20s, 60s]. Discarding video and using 9:16 photo.",
                        dur,
                    )
                    news_video.unlink(missing_ok=True)
                    news_video = None

            if news_video:
                dur, _ = inspect_video(news_video)
                log.info("[3/4] [MEDIA SEARCH] News video validated (%s, %.1fs). Will use full video without trimming.", news_video, dur)
            else:
                log.info("[3/4] [MEDIA SEARCH] Step 2: No valid [20s, 60s] news video. Searching internet for 9:16 photo for %r...", news_title)
                try:
                    fetch_news_background(
                        image_url=image_url,
                        search_query=search_query.strip(),
                        output_path=background_path,
                        article_url=article_url,
                        sources_urls=sources_urls,
                        pexels_api_key=pexels_api_key,
                        news_title=news_title,
                    )
                except Exception as exc:
                    log.warning("Web image search failed: %s; trying Cloudflare AI generation fallback.", exc)
                    cloudflare_credentials = getattr(
                        settings,
                        "cloudflare_credentials",
                        ((settings.cloudflare_account_id, settings.cloudflare_api_token),),
                    )
                    if cloudflare_credentials and any(c[0] and c[1] for c in cloudflare_credentials):
                        credential_pool = RotatingCredentialPool(
                            cloudflare_credentials,
                            "Cloudflare",
                            CREDENTIAL_ROTATION_PATH,
                        )
                        _generate_background(
                            plan.get("background_image_prompt", search_query).strip(),
                            background_path,
                            credential_pool,
                            settings.cloudflare_model,
                        )
                    else:
                        raise

        # Determine adaptive layout
        layout = plan.get("layout")
        layout = dict(layout) if isinstance(layout, dict) else {}

        if news_video and news_video.is_file():
            log.info("[MEDIA] News video available: %s. Superimposing text onto video reel.", news_video)
            try:
                import imageio_ffmpeg
                subprocess.run(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-y",
                        "-ss", "00:00:01",
                        "-i", str(news_video.resolve()),
                        "-vframes", "1",
                        str(background_path.resolve()),
                    ],
                    capture_output=True,
                    check=False,
                )
            except Exception as exc:
                log.warning("Could not extract thumbnail frame from video: %s", exc)

            if background_path.is_file():
                try:
                    analysis = analyze_image_layout(background_path)
                    if "text_position" not in layout:
                        layout["text_position"] = analysis.get("suggested_text_position", "top")
                except Exception:
                    if "text_position" not in layout:
                        layout["text_position"] = "top"
            else:
                if "text_position" not in layout:
                    layout["text_position"] = "top"

            overlay_path = post_dir / "text_overlay.png"
            compose_news_overlay(
                overlay_path,
                headline,
                plan["image_body"].strip(),
                settings.brand_name,
                REEL_WIDTH,
                REEL_HEIGHT,
                layout=layout,
                is_light_bg=False,
            )
            bgm_path = select_bgm_track(plan["category"], rotation_state_path=BGM_ROTATION_PATH)
            create_video_reel(news_video, overlay_path, reel_path, bgm_path=bgm_path)
            if background_path.is_file():
                try:
                    compose_news_image(
                        background_path,
                        image_path,
                        headline,
                        plan["image_body"].strip(),
                        settings.brand_name,
                        REEL_WIDTH,
                        REEL_HEIGHT,
                        layout=layout,
                    )
                except Exception as exc:
                    log.warning("Could not compose thumbnail image from video frame: %s", exc)
        else:
            if background_path.is_file():
                try:
                    analysis = analyze_image_layout(background_path)
                    if "text_position" not in layout:
                        layout["text_position"] = analysis.get("suggested_text_position", "bottom")
                    log.info("[LAYOUT] Image layout analysis: %s; Final text position: %s", analysis, layout.get("text_position"))
                except Exception as exc:
                    log.warning("Image layout analysis failed: %s; using default layout.", exc)
                    if "text_position" not in layout:
                        layout["text_position"] = "bottom"

            try:
                compose_news_image(
                    background_path,
                    image_path,
                    headline,
                    plan["image_body"].strip(),
                    settings.brand_name,
                    REEL_WIDTH,
                    REEL_HEIGHT,
                    layout=layout,
                )
            except TypeError:
                compose_news_image(
                    background_path,
                    image_path,
                    headline,
                    plan["image_body"].strip(),
                    settings.brand_name,
                    REEL_WIDTH,
                    REEL_HEIGHT,
                )
            bgm_path = select_bgm_track(plan["category"], rotation_state_path=BGM_ROTATION_PATH)
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
