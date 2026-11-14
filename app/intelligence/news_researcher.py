from datetime import datetime, timedelta, timezone


NEWS_CATEGORIES = (
    "Politics",
    "Geopolitics",
    "Crime",
    "Technology",
    "Business",
    "Defence",
    "Other",
    "Government schemes",
    "Social trends",
    "Major Sad Event",
    "Major Good Event",
)


def build_news_and_post_plan_prompt(
    previous_summaries: list[str] | None = None,
) -> str:
    india_timezone = timezone(timedelta(hours=5, minutes=30), name="IST")
    current_time = datetime.now(india_timezone).strftime("%A, %d %B %Y %H:%M IST")
    recent_summaries = previous_summaries or []
    if recent_summaries:
        recent_summaries_text = "\n".join(f"- {summary}" for summary in recent_summaries)
    else:
        recent_summaries_text = "- None yet."
    categories = ", ".join(f'"{category}"' for category in NEWS_CATEGORIES)

    return f"""The current date and time in India is {current_time}. Use browser search (`browser_search`) on every execution to collect the latest credible, high-impact news candidates available for an Indian audience. Remove duplicate reports of the same event and outdated stories before ranking. Compare several candidates, then create a single-image Reel plan only if the best story passes the strict publishing gate below. Audience interest is the highest priority. Avoid trivial stories, stale developments, manufactured trends and clickbait.

Search across India and international developments relevant to India: politics, geopolitics and diplomacy, crime and public safety, disasters and other major events, science and technology, business and consumer-impact news, government policy and schemes, social trends, sports, entertainment, notable achievements and uplifting human stories. These areas are examples rather than a checklist. Do not force a category.

Use a two-stage selection process and do not choose an overall winner before completing the category shortlist:
1. Category shortlist: evaluate each supported category ({categories}) independently. Within each category, compare eligible candidates and identify its single strongest candidate as that category's finalist. If no credible, fresh, non-duplicate candidate qualifies in a category, leave that category without a finalist. Assess candidates by public interest (how many people care or are affected), public attention/engagement potential, recency of the actual development, significance, and uniqueness.
2. Final selection: compare all category finalists and identify the highest-ranked available story. This ranking does NOT mean it qualifies for publication. Assess the highest-ranked story independently against the strict publishing gate below. Never lower the threshold to maintain posting frequency.

A story may be approved only when it has demonstrable, exceptionally broad India-wide relevance—not merely local/regional interest or a generic claim that everyone is affected. Look for credible evidence the issue affects or could materially affect people across India, multiple regions, or a very large share of the population. It must also have exceptionally high public interest, very strong potential for widespread public attention, a genuinely recent actual event/development (preferably breaking, no older than 24 hours), substantial real-world consequences, and meaningful uniqueness versus recent posts. It should make a broad audience pause and think, with a clear and authentic human/emotional stake (such as concern, hope, awe, empathy, or relief) that is supported by facts. Do not manufacture emotion, sensationalize suffering, or treat tragedy alone as an automatic reason to publish. A repeat underlying event qualifies only for a verified significant new development. Evaluate when the development actually occurred, not merely the article publication time. National decisions affecting millions, consequential constitutional judgments, major geopolitical/economic events, breakthroughs with broad consequences, and national emergencies are examples, not automatic approvals. Routine corporate news, ordinary product launches, minor statements, celebrity updates, routine sports results, old coverage, and interesting-but-low-impact stories should generally fail. A story may rank first and still be rejected. Prefer no post to a mediocre one.

Check primary or official sources where appropriate and corroborate consequential claims with credible reporting. Treat allegations, estimates, confirmed facts and unknowns distinctly. Never invent facts, quotations, publishers, article titles or URLs. Do not sensationalize crime, tragedy or suffering.

Before selecting, strictly exclude every candidate that repeats or substantially overlaps the same underlying news event as any of the latest 15 published-story summaries below, unless it has a verifiable, significant new development within the last 24 hours. Compare distinctive people, organizations, places, programs, event names and actions even when the headline wording differs. Do not evade this check with a secondary angle.

Recent published stories to exclude:
{recent_summaries_text}

Report your research counts based on candidates actually inspected: total collected and unique, current candidates after deduplication and freshness screening. Then report the highest-ranked story and a structured publish assessment. Use rating values LOW, MODERATE, HIGH, VERY_HIGH. Include eligible (boolean), india_wide_relevance, public_interest, public_attention, recency, significance, uniqueness, thought_provoking (boolean), emotional_resonance, human_stakes, india_wide_impact_evidence, emotional_resonance_evidence, actual_event_at (timezone-aware ISO 8601 timestamp for the actual latest development, or null if unknown), significant_new_development (boolean), new_development_summary, and reason. The two evidence fields must identify specific verifiable facts and source support; do not use generic assertions. Only approve when india_wide_relevance, public_interest, public_attention, recency, significance, uniqueness, and emotional_resonance are all VERY_HIGH; thought_provoking is true; human_stakes and both evidence fields are specific and evidence-supported; and actual_event_at is verified and within 24 hours. Do not approve a merely popular, emotional, or highest-ranked story without this broad significance. If no story qualifies or the highest-ranked story fails, set eligible false and explain why. Do not generate post copy, caption, hashtags, or image plan for rejected stories.

Do not claim a story is trending on a platform without evidence. Return exactly one selected story, not alternate final stories.

Only when the publish assessment is eligible true, return the full image plan:
- category: exactly one of {categories}. Use the spelling "Business".
- headline: specific, punchy and understandable without context, ideally 5-10 words.
- image_body: 25-45 words that make the image self-contained: say what happened, one or two key verified specifics, and why the audience should care. Keep it scannable and avoid clutter.
- background_image_prompt: a compelling editorial illustration supporting this exact news story, with one large recognizable focal subject, visual depth, and clear negative space behind the planned copy. Do not ask the image model to render text, letters, numbers, logos or watermarks. The illustration must not look like documentary evidence.
- description: a useful, engaging explanation of the news and its context, no more than 500 words. Clearly identify what is confirmed, uncertain or alleged as relevant. It will be used as the Instagram Reel caption. Include "Like, share and follow" exactly once. Do not include an unsupported claim.
- hashtags: 5-15 unique, relevant hashtags, each a single token beginning with #. Choose specific discoverability tags for this story and its Indian audience, not generic filler. The app appends them to the caption and validates the combined caption against Instagram's 2200-character limit.
- selected_story.event_summary: a factual, self-explanatory summary of the specific event in 5-15 words, for duplicate detection.
- sources: exact URLs, publishers, titles, publication dates and what each source supports.
Keep audience engagement high by making the visual and copy immediately clear, useful, emotionally appropriate and curiosity-provoking without clickbait, misleading omissions or excessive text. The image must contain the supplied headline and image_body as readable overlays, plus this exact callout: "See description for more detail". Include "Like, share and follow" on the image too. Do not put source URLs or the full long description on the image.

Return only one valid JSON object, no markdown or extra text. Always include research_metrics and publish_assessment. For an eligible story use this shape:
{{"research_metrics":{{"candidate_count":25,"unique_candidate_count":18}},"selected_story":{{"title":"...","event_summary":"5-15 word factual summary","published_at":"...","actual_event_at":"ISO 8601 timestamp with timezone","url":"https://...","source":"..."}}, "publish_assessment":{{"eligible":true,"india_wide_relevance":"VERY_HIGH","public_interest":"VERY_HIGH","public_attention":"VERY_HIGH","recency":"VERY_HIGH","significance":"VERY_HIGH","uniqueness":"VERY_HIGH","thought_provoking":true,"emotional_resonance":"VERY_HIGH","human_stakes":"Specific fact-based human consequences.","india_wide_impact_evidence":"Specific verified national or cross-regional impact.","emotional_resonance_evidence":"Specific fact-based reason the development resonates emotionally.","actual_event_at":"ISO 8601 timestamp with timezone","significant_new_development":false,"new_development_summary":"","reason":"..."}}, "category":"one exact allowed category","headline":"...","image_body":"...","background_image_prompt":"...","description":"...","hashtags":["#ExampleOne","#ExampleTwo","#ExampleThree","#ExampleFour","#ExampleFive"],"sources":[{{"name":"publisher","title":"article title","url":"https://...","published_at":"date","supports":"..."}}],"editorial_notes":[]}}

For a rejected or unavailable story, return only the research metrics, selected_story (the highest-ranked story object or null), and publish_assessment with eligible false and reason. Do not include post-plan fields. Never invent candidate counts, timestamps, sources, or facts."""
