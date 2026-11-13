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

    return f"""The current date and time in India is {current_time}. Search broadly using browser search and compare several candidates to identify the latest credible, high-impact news for an Indian audience, then create one Instagram single-image Reel plan for the strongest eligible story. Audience interest is the highest priority: choose news the public is likely to find genuinely interesting, enjoyable or useful, discuss, like and share. Look for meaningful real-world impact, broad relevance, a surprising but verified development, or an uplifting human outcome. Avoid trivial stories, stale developments, manufactured trends and clickbait.

Search across India and international developments relevant to India: politics, geopolitics and diplomacy, crime and public safety, disasters and other major events, science and technology, business and consumer-impact news, government policy and schemes, social trends, sports, entertainment, notable achievements and uplifting human stories. These areas are examples rather than a checklist. Do not force a category.

Use a two-stage selection process and do not choose an overall winner before completing the category shortlist:
1. Category shortlist: evaluate each supported category ({categories}) independently. Within each category, compare eligible candidates and identify its single strongest candidate as that category's finalist. If no credible, fresh, non-duplicate candidate qualifies in a category, leave that category without a finalist. Assess candidates using their combined real-world impact, broad public interest and appeal, and recency; do not reward a story merely for being the newest or most widely reported.
2. Final selection: compare all category finalists against one another and select exactly one final story with the strongest combined potential to attract public attention, generate engagement, and resonate with a broad Indian audience. Prioritize a compelling balance of impact, public interest and timeliness, rather than allowing recency or coverage volume alone to decide. Accuracy, freshness and responsible framing are mandatory.

Select only stories first reported or materially updated within the last 24 hours in India time. An older underlying event qualifies only when there is a specific, verifiable, important new development within the last 24 hours; explain that new development rather than recycling old coverage. Verify publication and update times, and reject stories without clear recent sourcing.

Check primary or official sources where appropriate and corroborate consequential claims with credible reporting. Treat allegations, estimates, confirmed facts and unknowns distinctly. Never invent facts, quotations, publishers, article titles or URLs. Do not sensationalize crime, tragedy or suffering.

Before selecting, strictly exclude every candidate that repeats or substantially overlaps the same underlying news event as any of the latest 15 published-story summaries below. For each candidate, identify its distinctive main keywords: people and organization names, places, products, programs, event names, and the specific action or development. Compare those keywords with every history entry even when wording, order, abbreviations or headline phrasing differ. If two or more distinctive keywords match and the candidate concerns the same subject, entity or development, treat it as a duplicate and reject it. Do not evade this check by changing the wording or focusing on a secondary angle of the same story. Keep searching until you find a genuinely unrelated eligible story; if no such high-interest story is available, return no story by clearly setting selected_story to null rather than repeating an old one.

Recent published stories to exclude:
{recent_summaries_text}

Apply duplicate exclusion before considering candidates for a category finalist, then compare only eligible category finalists in the final selection. Public interest, audience enjoyment, usefulness, discussion and sharing potential are top editorial priorities, balanced with real-world impact and timeliness. Do not claim a story is trending on a platform without evidence. Return one selected story, not alternate final stories.

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
