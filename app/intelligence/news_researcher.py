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

    return f"""The current date and time in India is {current_time}. Search broadly and compare several candidates using browser search to identify the latest credible, high-impact news for an Indian audience, then create one Instagram single-image Reel plan for the strongest eligible story. Audience interest is the highest priority: choose news the public is likely to find genuinely interesting, enjoyable or useful, discuss, like and share. Look for meaningful real-world impact, broad relevance, a surprising but verified development, or an uplifting human outcome. Avoid trivial stories, stale developments, manufactured trends and clickbait.

Search across India and international developments relevant to India: politics, geopolitics and diplomacy, crime and public safety, disasters and other major events, science and technology, business and consumer-impact news, government policy and schemes, social trends, sports, entertainment, notable achievements and uplifting human stories. These areas are examples rather than a checklist. Do not force a category.

Select only a story first reported or materially updated within the last 24 hours in India time. An older underlying event qualifies only when there is a specific, verifiable, important new development within the last 24 hours; explain that new development rather than recycling old coverage. Verify publication and update times, and reject stories without clear recent sourcing. Compare candidates and rank recency, public interest, audience relevance, actual impact and source quality; choose the freshest high-interest item, not merely the newest low-value headline.

Check primary or official sources where appropriate and corroborate consequential claims with credible reporting. Treat allegations, estimates, confirmed facts and unknowns distinctly. Never invent facts, quotations, publishers, article titles or URLs. Do not sensationalize crime, tragedy or suffering.

Before selecting, strictly exclude every candidate that repeats or substantially overlaps the same underlying news event as any of the latest 15 published-story summaries below. For each candidate, identify its distinctive main keywords: people and organization names, places, products, programs, event names, and the specific action or development. Compare those keywords with every history entry even when wording, order, abbreviations or headline phrasing differ. If two or more distinctive keywords match and the candidate concerns the same subject, entity or development, treat it as a duplicate and reject it. Do not evade this check by changing the wording or focusing on a secondary angle of the same story. Keep searching until you find a genuinely unrelated eligible story; if no such high-interest story is available, return no story by clearly setting selected_story to null rather than repeating an old one.

Recent published stories to exclude:
{recent_summaries_text}

After duplicate exclusion, choose the latest story that is both credible and likely to matter to or engage a broad Indian audience. Public interest and audience enjoyment, usefulness, discussion and sharing potential are the top editorial priorities, while accuracy and responsible framing are mandatory. Do not claim a story is trending on a platform without evidence. Return one selected story, not alternate stories or a shortlist.

Return one structured image plan:
- category: exactly one of {categories}. Use the spelling "Business".
- headline: specific, punchy and understandable without context, ideally 5-10 words.
- image_body: 25-45 words that make the image self-contained: say what happened, one or two key verified specifics, and why the audience should care. Keep it scannable and avoid clutter.
- background_image_prompt: a compelling editorial illustration supporting this exact news story, with one large recognizable focal subject, visual depth, and clear negative space behind the planned copy. Do not ask the image model to render text, letters, numbers, logos or watermarks. The illustration must not look like documentary evidence.
- description: a useful, engaging explanation of the news and its context, no more than 500 words. Clearly identify what is confirmed, uncertain or alleged as relevant. It will be used as the Instagram Reel caption. Include "Like, share and follow" exactly once. Do not include an unsupported claim.
- hashtags: 5-15 unique, relevant hashtags, each a single token beginning with #. Choose specific discoverability tags for this story and its Indian audience, not generic filler. The app appends them to the caption and validates the combined caption against Instagram's 2200-character limit.
- selected_story.event_summary: a factual, self-explanatory summary of the specific event in 5-15 words, for duplicate detection.
- sources: exact URLs, publishers, titles, publication dates and what each source supports.
Keep audience engagement high by making the visual and copy immediately clear, useful, emotionally appropriate and curiosity-provoking without clickbait, misleading omissions or excessive text. The image must contain the supplied headline and image_body as readable overlays, plus this exact callout: "See description for more detail". Include "Like, share and follow" on the image too. Do not put source URLs or the full long description on the image.

Return only one valid JSON object, no markdown or extra text, using this shape:
{{"selected_story":{{"title":"...","event_summary":"5-15 word factual summary","published_at":"...","url":"https://...","source":"...","trend_rationale":"..."}}, "category":"one exact allowed category","headline":"...","image_body":"...","background_image_prompt":"...","description":"...","hashtags":["#ExampleOne","#ExampleTwo","#ExampleThree","#ExampleFour","#ExampleFive"],"sources":[{{"name":"publisher","title":"article title","url":"https://...","published_at":"date","supports":"..."}}],"editorial_notes":[]}}

If no fresh, high-interest, credible, non-duplicate story exists, return {{"selected_story":null,"reason":"...","sources":[]}}; do not invent or repeat a story."""
