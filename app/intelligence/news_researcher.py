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
    cooldown_exceptions = ", ".join(f'"{exception}"' for exception in COOLDOWN_EXCEPTIONS)

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

    return f"""At {current_time}, browser_search for 5 latest credible, trending, high-impact India stories in {categories}. Prefer verified events from the last 5 hours; otherwise top major verified developments from the last 24 hours. Use occurrence time, not article time. Rank by recency, impact and public interest. Report actual candidate/unique counts. Verify facts, distinguish allegations, and never invent facts, counts, URLs or sources.

SEARCH: Search across categories; avoid repeat queries/full pages if snippets suffice. Compare supplied history, not old stories. Stop at 5 unique candidates after verifying selected claims and event time.

DUPES: Compare candidates with all 15 latest posted stories by event, people, organizations, places and actions—not wording. A shared distinctive name/keyword means repeat; exclude unless a source confirms a separate event; if unsure, exclude.
{history}

PUBLISH: {publish_rule} During the 3-hour cooldown, only source-backed RARE_EVENT, UNUSUAL_NUMBERS, SAD_EVENT, MAJOR_ACHIEVEMENT, PUBLIC_AWARENESS or MAJOR_CHANGE qualifies; otherwise NONE. Recurring news or tragedy alone doesn't qualify.

OUTPUT: JSON only; no candidate list/prose. Always include top-level research_metrics {{candidate_count: integer, unique_candidate_count: integer}}, selected_story {{title, event_summary, actual_event_at}} and publish_assessment {{eligible: boolean, reason: string, cooldown_exception: one string from [{cooldown_exceptions}], cooldown_exception_evidence: string}}. Event summary: factual, 5-15 words; actual_event_at: source-supported ISO 8601 or null. If rejected, omit plan fields and return best candidate or null. If approved, add category, headline, image_body, background_image_prompt, description, hashtags and sources as top-level siblings of selected_story; never nest them. Keep reason/evidence concise.

PLAN: Category selects BGM. Use a punchy headline and self-contained 25-45-word image_body. Create bright, vivid, colorful, relevant art with luminous light, a focal point and negative space—not dim, muddy or monochrome. Background has no text, lettering, signage, logos, watermarks or labels. App flexibly overlays “The Daily Chronicle”, headline, body and “Like, share and follow”. Factual description, max 500 words, with “Like, share and follow” once. Add 5-15 unique, relevant single-token hashtags and at least one credible source with an exact URL."""
