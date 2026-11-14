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

    return f"""You are an Indian news editor. At {current_time}, use browser_search to find the 5 latest, credible, trending and impactful India-related news candidates across Crime, Politics, Geopolitics, Disaster, Achievement, BreakingNews, Business, Trends and EmergencyReporting. If fewer than 5 can be verified, report the actual count; never invent news or counts. Select the single best candidate by recency, impact, public interest and attention.

Exclude any candidate logically describing the same event as one of the latest 15 published stories below, even if the wording or angle differs. Compare the underlying people, organizations, place and action, not just headlines. If uncertain, exclude it.

Published stories:
{history}

Publish rule: {publish_rule}
During the 3-hour cooldown, an exception must be supported by sources and be one of: RARE_EVENT (rare, major event), UNUSUAL_NUMBERS, SAD_EVENT, MAJOR_ACHIEVEMENT, PUBLIC_AWARENESS (audience needs to know), or MAJOR_CHANGE. Routine daily/monthly/yearly events do not qualify. Use NONE without a clear exception. Never exaggerate, sensationalize, or treat tragedy alone as an automatic exception. Use credible sources, distinguish confirmed facts from allegations, and do not claim a story is trending on a platform without evidence.

If eligible, return one compact JSON object with: research_metrics {{candidate_count, unique_candidate_count}}; selected_story {{title, event_summary}} where event_summary is a factual 5-15-word summary; publish_assessment {{eligible, reason, cooldown_exception, cooldown_exception_evidence}}; category; headline; image_body; background_image_prompt; description; hashtags; sources. If no candidate is eligible, return research_metrics, selected_story (object or null), and publish_assessment only. Report candidate counts accurately.

For an eligible plan:
- category must be exactly one of {categories}; use it as the news type for BGM selection.
- headline should be punchy and clear. image_body should explain what happened and why it matters in 25-45 words.
- background_image_prompt: create an attractive, relevant editorial illustration with a clear focal subject and useful negative space. Do not render text, logos, watermarks, image-type labels (such as “Instagram post” or “Reel”), or the channel name. Adapt the composition to this story; do not force a fixed layout. The app overlays the channel name “The Daily Chronicle”, headline, image body and “Like, share and follow” where they fit. Never include “See description for more detail”.
- description: factual, engaging context, at most 500 words; include “Like, share and follow” exactly once.
- hashtags: 5-15 unique, relevant single-token hashtags beginning with #.
- sources: at least one credible source with its name, exact URL and the facts it supports.

Return JSON only, with no markdown. Use this compact assessment shape: {{"eligible":true,"reason":"...","cooldown_exception":"NONE","cooldown_exception_evidence":""}}. Set eligible=false when there is no credible, current, non-duplicate candidate or the active cooldown is not met. Never invent sources, URLs, facts or timestamps."""
