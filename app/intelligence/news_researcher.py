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

    return f"""The current date and time in India is {current_time}. Use browser search to identify and compare the latest credible, high-impact and genuinely interesting news for an Indian audience, then create one Instagram single-image Reel plan for the single strongest eligible story. Preserve the existing story-selection approach: search broadly, compare several candidates, prioritize high impact and real audience interest, and prefer the latest substantive development. Prefer reports published or materially updated in the last 24 hours; older events qualify only if a significant new development happened in that period. Verify publication and update times.

Search across the same range of India and international developments relevant to India: politics, geopolitics and diplomacy, crime and public safety, disasters and other major events, science and technology, business and consumer-impact news, government policy and schemes, social trends, sports, entertainment, notable achievements and uplifting human stories. These areas are examples rather than a checklist. Do not force a category or choose a trivial recent item over an important development.

Check primary or official sources where appropriate and corroborate consequential claims with credible reporting. Treat allegations, estimates, confirmed facts and unknowns distinctly. Never invent facts, quotations, publishers, article titles or URLs. Do not sensationalize crime, tragedy or suffering.

Before selecting, exclude any candidate about the same underlying news event as any item in this recent-final-stories list, even if the headlines differ. Compare the people, organizations, location, event and core development. If a candidate overlaps, keep searching for the strongest credible, verifiable non-overlapping event:
{recent_summaries_text}

After excluding overlaps, choose the strongest story by weighing significance and real-world impact, timeliness, relevance to Indian audiences, credible evidence and genuine audience interest. Do not claim a story is trending on a platform without evidence. Return one selected story, not alternate stories or a shortlist.

Return one structured image plan:
- category: exactly one of {categories}. Use the spelling "Business".
- headline: specific, punchy and understandable without context, ideally 5-10 words.
- image_body: 25-45 words that make the image self-contained: say what happened, one or two key verified specifics, and why the audience should care. Keep it scannable and avoid clutter.
- background_image_prompt: a compelling editorial illustration supporting this exact news story, with one large recognizable focal subject, visual depth, and clear negative space behind the planned copy. Do not ask the image model to render text, letters, numbers, logos or watermarks. The illustration must not look like documentary evidence.
- description: a useful, engaging explanation of the news and its context, no more than 500 words and also no more than 2200 characters so it fits Instagram. Clearly identify what is confirmed, uncertain or alleged as relevant. It will be used as the Instagram Reel caption. Include "Like, share and follow" exactly once. Do not include an unsupported claim.
- selected_story.event_summary: a factual, self-explanatory summary of the specific event in 5-15 words, for duplicate detection.
- sources: exact URLs, publishers, titles, publication dates and what each source supports.
Keep audience engagement high by making the visual and copy immediately clear, useful, emotionally appropriate and curiosity-provoking without clickbait, misleading omissions or excessive text. The image must contain the supplied headline and image_body as readable overlays, plus this exact callout: "See description for more detail". Include "Like, share and follow" on the image too. Do not put source URLs or the full long description on the image.

Return only one valid JSON object, no markdown or extra text, using this shape:
{{"selected_story":{{"title":"...","event_summary":"5-15 word factual summary","published_at":"...","url":"https://...","source":"...","trend_rationale":"..."}},"category":"one exact allowed category","headline":"...","image_body":"...","background_image_prompt":"...","description":"...","sources":[{{"name":"publisher","title":"article title","url":"https://...","published_at":"date","supports":"..."}}],"editorial_notes":[]}}"""
