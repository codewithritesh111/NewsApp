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
    # Legacy categories kept for backwards compatibility
    "Government schemes",
    "Social trends",
    "Major Sad Event",
    "Major Good Event",
    "Technology",
    "Defence",
)

COOLDOWN_EXCEPTIONS = (
    "NONE",
    "BREAKING_NEWS",
    "NATIONWIDE_IMPACT",
    "DISASTER",
    "MAJOR_ACHIEVEMENT",
    "RARE_EVENT",
    "UNUSUAL_NUMBERS",
    "SAD_EVENT",
    "PUBLIC_AWARENESS",
    "MAJOR_CHANGE",
)


def build_news_and_post_plan_prompt(
    previous_summaries: list[str] | None = None,
    posting_cooldown_active: bool = False,
) -> str:
    india_timezone = timezone(timedelta(hours=5, minutes=30), name="IST")
    current_time = datetime.now(india_timezone).strftime("%A, %d %B %Y %H:%M IST")
    recent_summaries = (previous_summaries or [])[-15:]
    if recent_summaries:
        recent_summaries_text = "\n".join(f"- {summary}" for summary in recent_summaries)
    else:
        recent_summaries_text = "- None yet."

    allowed_topics = (
        '"Crime", "Politics", "Geopolitics", "Disaster", "Achievement", '
        '"BreakingNews", "Business", "Trends", "EmergencyReporting", "Other"'
    )

    if posting_cooldown_active:
        cooldown_instructions = """POSTING COOLDOWN STATUS: ACTIVE (at least one post/reel was published within the last 3 hours).
CRITICAL COOLDOWN PUBLISHING RULES:
Since a post/reel was published in the last 3 hours, the pipeline is in cooldown.
- ABSOLUTE EXCLUSIONS: Do NOT publish any small crime (e.g. local theft, isolated robbery, minor fraud, local arrest), small local event, minor incident, or routine/regular event (daily/monthly/yearly occurrences). All small crimes and minor incidents MUST BE REJECTED during cooldown.
- STRICT COOLDOWN EXCEPTION: Publish ONLY if the candidate is genuinely very important, such as a major BreakingNews event, has verified nationwide impact across India, is a major Disaster, or a major historical Achievement:
    * BREAKING_NEWS: Very important breaking news event of urgent national concern.
    * NATIONWIDE_IMPACT: Verified nationwide impact affecting people or policy across India.
    * DISASTER: Major natural or human disaster, major tragedy, or national emergency.
    * MAJOR_ACHIEVEMENT: Big historical or national achievement / major breakthrough.
    * RARE_EVENT: A greater event that occurs once in a long while.
    * UNUSUAL_NUMBERS: Involves big, unusual, or shocking numbers.
    * MAJOR_CHANGE: Big systemic changes took place (landmark law, major constitutional ruling).
- If the candidate meets these strict criteria, set eligible to true, nationwide_impact to true, is_minor_or_local_incident to false, and set cooldown_exception to the matching code.
- Rule 3: If the candidate is a small crime, small event, minor incident, or lacks nationwide importance, set eligible to false, is_minor_or_local_incident to true, cooldown_exception to "NONE", and explain the rejection reason."""
    else:
        cooldown_instructions = """POSTING COOLDOWN STATUS: INACTIVE (no post/reel through this pipeline was published in the last 3 hours).
Publishing Rules:
- Rule 1: If no post/reel through this pipeline is published in last 3 hrs, approve and publish the post/reel (set eligible to true, cooldown_exception to "NONE", and is_minor_or_local_incident to false).
- If no credible candidate exists or all candidates are duplicates, set eligible to false and explain why."""

    return f"""The current date and time in India is {current_time}.

Follow this exact process to research and shortlist news for The Daily Chronicle Instagram channel:

1. COLLECT CANDIDATES (TOKEN-EFFICIENT SEARCH):
- Execute ONLY 1 or 2 focused browser search queries (e.g. `browser_search('latest breaking trending news India')`).
- Inspect the top search result snippets directly. Do NOT make repetitive multi-category searches and do NOT fetch full web pages.
- Collect exactly 5 latest, trending, impactful, attention-drawing Indian news stories from topics like Crime, Politics, Geopolitics, Disaster, Achievement, BreakingNews, Business, Trends, EmergencyReporting. These 5 stories are your candidates.

2. DEDUPLICATION & SHORTLISTING:
- Critical requirement: strictly ensure the selected candidate is NOT logically similar to any of the last 15 news posted below. Posting duplicate or logically similar stories is strictly prohibited. Compare distinctive people, organizations, places, event names, and actions even if worded differently.
Last 15 news posted:
{recent_summaries_text}

- From the eligible candidates that pass deduplication, select the single best candidate (the Shortlisted Candidate).

3. PUBLISH GATE DECISION:
{cooldown_instructions}

4. OUTPUT FORMAT:
If the reel is finalized to be posted (eligible is true):
- category: Classify the news into exactly one of [{allowed_topics}].
- headline: Specific, punchy headline understandable without context, 5-10 words.
- image_body: 25-45 words describing what happened, key verified specifics, and why the audience should care.
- background_image_prompt: Create a plan of the image to be generated for the post. The generated image MUST be bright, vibrant, and eye-catching with a good, attractive, and colorful background directly relevant to this news story so people's interest will increase. Use bright cinematic lighting, rich colors, and clear visual depth. The image plan should NOT have any image type label in the image, and must NOT be a fixed format—it must be flexible for the app to overlay: Name of news channel (The Daily Chronicle), headline, content, and "Like, Share and Follow" wherever suits. Do NOT ask for text, lettering, words, logos, or watermarks in the background image. Do NOT add "See description for more detail" in the plan or image.
- description: An engaging explanation of the news (max 500 words) for the Instagram caption. Must include "Like, share and follow" exactly once.
- hashtags: 5-15 relevant hashtags starting with #.
- selected_story: factual title, 5-15 word event_summary, url, and source.
- sources: list of source articles.

If eligible is false:
- Return research_metrics, selected_story (the best candidate or null), and publish_assessment with eligible: false and reason. Do not generate headline, image_body, background_image_prompt, description, or hashtags.

Return ONLY one valid JSON object (no markdown, no backticks, no extra text):
{{
  "research_metrics": {{"candidate_count": 5, "unique_candidate_count": 5}},
  "selected_story": {{
    "title": "Title of shortlisted candidate",
    "event_summary": "5-15 word factual summary of the event",
    "published_at": "...",
    "url": "https://...",
    "source": "Publisher Name"
  }},
  "publish_assessment": {{
    "eligible": true,
    "cooldown_active": {str(posting_cooldown_active).lower()},
    "cooldown_exception": "NONE",
    "nationwide_impact": true,
    "is_minor_or_local_incident": false,
    "reason": "Clear explanation of the publishing decision"
  }},
  "category": "BreakingNews",
  "headline": "Punchy Specific News Headline",
  "image_body": "25 to 45 words explaining what happened, confirmed facts, and why the audience should care about this news development.",
  "background_image_prompt": "A bright, vibrant, and visually striking editorial illustration with an attractive, colorful background relevant to this news story, bright cinematic lighting, high clarity, and clean negative space flexible for text overlays. No text or logos.",
  "description": "Engaging description explaining the story and context. Like, share and follow.",
  "hashtags": ["#News", "#India", "#BreakingNews", "#LatestUpdates", "#CurrentAffairs"],
  "sources": [{{"name": "Publisher", "title": "Article Title", "url": "https://..."}}]
}}"""
