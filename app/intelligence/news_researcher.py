from datetime import datetime, timedelta, timezone


NEWS_CATEGORIES = (
    "Crime",
    "Politics",
    "Geopolitics",
    "Disaster",
    "Achievement",
    "BreakingNews",
    "Business",
    "Trends",
    "EmergencyReporting",
    "Other",
)

COOLDOWN_EXCEPTIONS = (
    "NONE",
    "RARE_EVENT",
    "UNUSUAL_NUMBERS",
    "SAD_EVENT",
    "MAJOR_ACHIEVEMENT",
    "PUBLIC_AWARENESS",
    "MAJOR_CHANGE",
)


def build_news_and_post_plan_prompt(
    previous_summaries: list[str] | None = None,
    posting_cooldown_active: bool = False,
) -> str:
    india_timezone = timezone(timedelta(hours=5, minutes=30), name="IST")
    current_time = datetime.now(india_timezone).strftime("%d %b %Y %H:%M IST")
    recent_summaries = previous_summaries or []
    history = "\n".join(f"- {summary}" for summary in recent_summaries) or "- None"
    categories = ", ".join(f'"{category}"' for category in NEWS_CATEGORIES)

    if posting_cooldown_active:
        publish_rule = (
            "A Reel was posted within 3 hours. Publish only for a substantiated "
            "cooldown_exception; otherwise set eligible=false and omit the post plan."
        )
    else:
        publish_rule = (
            "No Reel was posted within 3 hours: publish the best valid candidate, "
            "including routine news."
        )

    return f"""Indian news editor. At {current_time}, use browser_search to find 5 latest credible, trending, impactful India-related candidates in {categories}. Report actual candidate/unique counts; never invent them. Strongly prioritize verified events from the last 5 hours; if impact is similar, rank the newer event higher. Use occurrence time, not article time. Select the best by recency, impact, public interest and attention.

REPEATS: Compare candidates with all 15 latest posted stories below by event, people, organizations, places and actions—not headline wording alone. One shared distinctive name or keyword flags a repeat: exclude unless sources prove it is a separate event; if unsure, exclude.
{history}

PUBLISH: {publish_rule} During the 3-hour cooldown, only a source-backed RARE_EVENT (rare, major event), UNUSUAL_NUMBERS, SAD_EVENT, MAJOR_ACHIEVEMENT, PUBLIC_AWARENESS or MAJOR_CHANGE qualifies; otherwise use NONE. Routine recurring news and tragedy alone do not qualify. Verify claims, distinguish allegations from facts, and do not claim unsupported platform trends.

OUTPUT: Return JSON only. Always include research_metrics {{candidate_count, unique_candidate_count}}, selected_story {{title, event_summary, actual_event_at}} (event_summary: factual, 5-15 words; actual_event_at: source-supported occurrence time in ISO 8601 or null), and publish_assessment {{eligible, reason, cooldown_exception, cooldown_exception_evidence}}. If rejected, omit plan fields and return the best candidate or null. If eligible, also include category, headline, image_body, background_image_prompt, description, hashtags and sources. Never invent facts, counts, URLs or sources.

PLAN: category is one of the listed types and selects BGM. Make the headline punchy; image_body is self-contained, 25-45 words. Create a bright, vivid, colorful, story-relevant illustration with luminous light, a focal point and negative space; avoid dim, muddy or monochrome colors. Background must contain no text, lettering, signage, logos, watermarks or labels. Layout is flexible; the app adds “The Daily Chronicle”, headline, body and “Like, share and follow”. Description: factual context, max 500 words, include “Like, share and follow” once. Add 5-15 unique, relevant, single-token hashtags and at least one credible source with exact URL and supported facts."""
