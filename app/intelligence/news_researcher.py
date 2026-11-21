import re
from datetime import datetime, timedelta, timezone

NEWS_CATEGORIES = (
    "ShockingCrime",
    "NaturalDisaster",
    "MajorAchievement",
    "BreakingNews",
    "CelebritySports",
    "ScienceTech",
    "Unusual",
    "Business",
    "Trends",
    "EmergencyReporting",
    "Other",
    # Backward-compatible categories
    "Crime",
    "Politics",
    "Geopolitics",
    "Disaster",
    "Achievement",
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

APPROVED_NEWS_CHANNELS = (
    "Aaj Tak",
    "NDTV",
    "ABP News",
    "India Today TV",
    "Times Now",
    "CNN-News18",
    "Republic TV",
    "Zee News",
    "India TV",
    "News18 India",
    "DD News",
    "TV9 Bharatvarsh",
    "News24",
    "Times Now Navbharat",
    "WION",
)

CHANNEL_ALIASES: dict[str, str] = {
    "aaj tak": "Aaj Tak",
    "aajtak": "Aaj Tak",
    "aaj tak hd": "Aaj Tak",
    "ndtv": "NDTV",
    "ndtv 24x7": "NDTV",
    "ndtv india": "NDTV",
    "ndtv profit": "NDTV",
    "abp": "ABP News",
    "abp news": "ABP News",
    "abp live": "ABP News",
    "abp ananda": "ABP News",
    "india today": "India Today TV",
    "india today tv": "India Today TV",
    "indiatoday": "India Today TV",
    "times now": "Times Now",
    "timesnow": "Times Now",
    "times now world": "Times Now",
    "cnn-news18": "CNN-News18",
    "cnn news18": "CNN-News18",
    "news18": "CNN-News18",
    "news 18": "CNN-News18",
    "republic": "Republic TV",
    "republic tv": "Republic TV",
    "republic bharat": "Republic TV",
    "republic world": "Republic TV",
    "zee": "Zee News",
    "zee news": "Zee News",
    "zee bharat": "Zee News",
    "zeenews": "Zee News",
    "india tv": "India TV",
    "indiatv": "India TV",
    "indiatv news": "India TV",
    "news18 india": "News18 India",
    "dd news": "DD News",
    "doordarshan": "DD News",
    "doordarshan news": "DD News",
    "tv9": "TV9 Bharatvarsh",
    "tv9 bharatvarsh": "TV9 Bharatvarsh",
    "tv9 bharat": "TV9 Bharatvarsh",
    "news24": "News24",
    "news 24": "News24",
    "times now navbharat": "Times Now Navbharat",
    "times now navbharat hd": "Times Now Navbharat",
    "wion": "WION",
    "wion news": "WION",
}


def match_approved_channel(name: str) -> str | None:
    """Normalize and match a channel name against the 15 approved Indian news channels."""
    if not name or not isinstance(name, str):
        return None
    cleaned = name.strip().lower()
    cleaned_simple = re.sub(r"\.(?:com|in|org|net)\b", "", cleaned)
    cleaned_simple = re.sub(r"\b(?:online|live|digital|english|hindi|hd|news|tv)\b", "", cleaned_simple).strip()
    if cleaned in CHANNEL_ALIASES:
        return CHANNEL_ALIASES[cleaned]
    if cleaned_simple in CHANNEL_ALIASES:
        return CHANNEL_ALIASES[cleaned_simple]
    for alias, canonical in CHANNEL_ALIASES.items():
        if alias in cleaned or alias in cleaned_simple:
            return canonical
    return None


def build_news_and_post_plan_prompt(
    previous_summaries: list[str] | None = None,
    posting_cooldown_active: bool = False,
) -> str:
    india_timezone = timezone(timedelta(hours=5, minutes=30), name="IST")
    now_ist = datetime.now(india_timezone)
    current_time = now_ist.strftime("%A, %d %B %Y %H:%M IST")
    today_str = now_ist.strftime("%d %B %Y")
    recent_summaries = (previous_summaries or [])[-15:]
    if recent_summaries:
        recent_summaries_text = "\n".join(f"- {summary}" for summary in recent_summaries)
    else:
        recent_summaries_text = "- None yet."

    categories_list = (
        "ShockingCrime, NaturalDisaster, MajorAchievement, BreakingNews, "
        "CelebritySports, ScienceTech, Unusual, Business, Trends, EmergencyReporting, "
        "Crime, Politics, Geopolitics, Disaster, Achievement, Other"
    )

    if posting_cooldown_active:
        cooldown_instructions = (
            "POSTING COOLDOWN STATUS: ACTIVE (at least one post/reel published in last 3 hrs).\n"
            "- Reject small crime, minor incident, or routine events.\n"
            "- Approve ONLY major BreakingNews, national disasters, or historical achievements.\n"
            "- Set cooldown_exception to matching code (BREAKING_NEWS, NATIONWIDE_IMPACT, DISASTER, MAJOR_ACHIEVEMENT, etc.) or NONE."
        )
    else:
        cooldown_instructions = (
            "POSTING COOLDOWN STATUS: INACTIVE (no post/reel published in last 3 hrs).\n"
            "- Approve high-impact news (eligible: true, cooldown_exception: 'NONE', is_minor_or_local_incident: false)."
        )

    channels_list = "\n".join(f"  {idx}. {ch}" for idx, ch in enumerate(APPROVED_NEWS_CHANNELS, 1))

    return f"""The current date and time in India is {current_time}.

Follow this exact process to research and shortlist news for The Daily Chronicle Instagram channel:

1. COLLECT CANDIDATES (TOKEN-EFFICIENT SEARCH):
- Execute ONLY 1 focused browser search query targeting today's date: `browser_search('India breaking news live updates {today_str} Aaj Tak NDTV India Today Times Now')`.
- Inspect the top search snippets directly. Do NOT make repetitive searches and do NOT fetch full web pages.
- Collect exactly 5 latest, trending, impactful, attention-drawing Indian news stories.
- STRICT 5-HOUR RECENCY LIMIT (STRICTLY <= 5 HOURS OLD):
  * The candidate to select MUST NOT be older than 5 hours strictly from the current time ({current_time}).
  * Check the timestamp or relative time in search snippets (e.g. '1 hour ago', '2 hours ago', '3 hours ago', '4 hours ago').
  * STRICTLY REJECT any story published more than 5 hours ago or from earlier days/months/years.
  * ZERO TOLERANCE FOR OLD NEWS: Do NOT select past controversies, old scandals, or past chargesheets (e.g. past NEET paper leak cases, past elections, old events).
  * You MUST extract the genuine article URL and real publication time from the search result. Do NOT fabricate or invent timestamps.
- SELECTION CRITERIA (18-35 YEARS AGE FOCUS):
  * Target audience is 18-35 years old (Gen-Z and young Millennials) on Instagram. The news must be latest, trending, high-impact, and captivating.
  * Categories: {categories_list}.

2. DEDUPLICATION & 3-CHANNEL SHORTLISTING:
- STRICT REPETITION RESTRICTION: The selected story must strictly avoid repetition. Never select any story logically similar to the last 15 news posted below. Compare distinctive people, organizations, places, event names, and core actions even if worded differently:
Last 15 news posted:
{recent_summaries_text}

- MANDATORY 3-CHANNEL VERIFICATION MANDATE:
  From your candidates, select the single best candidate story (the Shortlisted Candidate) that was published within the last 5 hours AND LOGICALLY HAS BEEN PUBLISHED by AT LEAST 3 distinct channels from this approved list of 15 popular Indian news channels (i.e. the same underlying event is verified and reported by at least 3 of these channels, even if headline wording differs):
{channels_list}
  Verify coverage for your single shortlisted candidate only (run at most 1 quick search if needed, e.g. `browser_search('<event keywords> {today_str} Aaj Tak NDTV Times Now')`).
  Do NOT perform multiple searches for other candidates. Ensure your single shortlisted candidate is confirmed across at least 3 approved channels from this exact list.
  MEDIA SOURCING PREFERENCE: First check if a news broadcast video clip is available (set video_url if found); if no video is found, set video_url to null and provide an image_search_query.

3. PUBLISH GATE DECISION:
{cooldown_instructions}

4. OUTPUT FORMAT:
Return ONLY one valid JSON object with 5-15 relevant hashtags (no markdown, no extra text):
{{
  "research_metrics": {{"candidate_count": 5, "unique_candidate_count": 5}},
  "selected_story": {{
    "title": "Shortlisted candidate title",
    "event_summary": "5-15 word factual summary of the event",
    "published_at": "ISO timestamp or relative time (e.g. '2 hours ago') proving it is <= 5 hours old",
    "url": "https://...",
    "source": "Channel Name"
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
  "verified_channels": ["Aaj Tak", "NDTV", "India Today TV"],
  "headline": "Specific punchy headline, 5-10 words",
  "image_body": "10-25 words — single punchy line explaining what happened and why it matters.",
  "image_url": null,
  "video_url": null,
  "image_search_query": "Specific 3-5 word query for real camera-captured ground photo",
  "layout": {{
    "text_position": "top",
    "highlight_words": ["WORD1", "WORD2"]
  }},
  "description": "Engaging description (<500 words) for Instagram caption. Must include 'Like, share and follow' exactly once.",
  "hashtags": ["#News", "#India", "#BreakingNews", "#Trending", "#CurrentAffairs"],
  "sources": [
    {{"name": "Aaj Tak", "title": "Article Title 1", "url": "https://..."}},
    {{"name": "NDTV", "title": "Article Title 2", "url": "https://..."}},
    {{"name": "India Today TV", "title": "Article Title 3", "url": "https://..."}}
  ]
}}"""
