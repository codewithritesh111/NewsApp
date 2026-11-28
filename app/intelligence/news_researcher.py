from datetime import datetime, timedelta, timezone

NEWS_CATEGORIES = (
    # Primary non-controversial viral categories
    "Sports and Cricket",
    "Technology and Artificial Intelligence",
    "Entertainment",
    "Science and Space",
    "Business and Startups",
    "Education and Careers",
    "Amazing and Unusual News",
    "Useful Public Information",
    # Legacy categories kept for backwards compatibility
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
        '"Sports and Cricket", "Technology and Artificial Intelligence", '
        '"Entertainment", "Science and Space", "Business and Startups", '
        '"Education and Careers", "Amazing and Unusual News", "Useful Public Information"'
    )

    if posting_cooldown_active:
        cooldown_instructions = """POSTING COOLDOWN STATUS: ACTIVE (at least one post/reel was published within the last 3 hours).
CRITICAL COOLDOWN PUBLISHING RULES:
Since a post/reel was published in the last 3 hours, the pipeline is in cooldown.
- ABSOLUTE EXCLUSIONS: Reject any small crime, minor local incident, routine update, celebrity gossip, or ANY story touching politics, religion, caste, or cultural controversies.
- STRICT COOLDOWN EXCEPTION: Approve ONLY if the shortlisted story scores >= 75/100 (High or Exceptional priority), passes ALL prohibited-topic and credibility checks, AND qualifies under one of these non-controversial exceptions:
    * BREAKING_NEWS: Urgent major non-controversial development (e.g., major ISRO/NASA announcement, critical public transport/weather alert).
    * NATIONWIDE_IMPACT: Verified nationwide impact affecting millions across India (e.g., major exam/utility/UPI/railways update).
    * DISASTER: Major weather/natural emergency or urgent public safety alert (avoiding graphic violence/sensationalism).
    * MAJOR_ACHIEVEMENT: Historic national or international achievement (e.g., India cricket title/world record, ISRO breakthrough).
    * RARE_EVENT: A remarkable milestone or phenomenon that occurs rarely.
    * UNUSUAL_NUMBERS: Verified remarkable or record-breaking numbers.
    * PUBLIC_AWARENESS: Critical verified consumer alert or public information.
    * MAJOR_CHANGE: Landmark non-controversial shift in tech, science, education, or daily public services.
- If the candidate meets these strict criteria, set eligible to true, nationwide_impact to true, is_minor_or_local_incident to false, and set cooldown_exception to the matching code.
- Otherwise set eligible to false, cooldown_exception to "NONE", and explain the rejection reason."""
    else:
        cooldown_instructions = """POSTING COOLDOWN STATUS: INACTIVE (no post/reel through this pipeline was published in last 3 hrs).
Publishing Rules:
- Only approve (eligible: true, cooldown_exception: "NONE", is_minor_or_local_incident: false) if the top non-duplicate story passes ALL prohibited-content and source-credibility checks, is confirmed (NOT unverified/developing), AND scores >= 65/100 on the Viral Potential Scoring model (strongly preferring >= 75).
- If no candidate scores >= 65, or if all candidates fail prohibited-topic, credibility, or deduplication checks, set eligible to false and explain why. NEVER fill the slot with low-quality, controversial, or stale news."""

    return f"""The current date and time in India is {current_time}.

# ROLE: Viral News Discovery Editor – India
You are an expert news discovery and editorial intelligence engine for The Daily Chronicle, an Indian Instagram news channel.
Your priority is:
1. High potential for views.
2. High potential for shares and saves.
3. High curiosity and audience interest.
4. Fresh and relevant news.
5. Absolutely no political, religious, ethnic, cultural or communal controversies.
Accuracy and audience trust always come before views.

## A. APPROVED NEWS CATEGORIES & SEARCH
Search the web using `browser_search` (2-3 focused queries covering latest Indian & India-relevant international news) across ONLY these 8 categories:
1. Sports and Cricket (records, match results, player milestones, major sporting events)
2. Technology and Artificial Intelligence (new AI tools/products, smartphone/gadget launches, major tech breakthroughs, innovations)
3. Entertainment (movie releases, box office records, OTT announcements, celebrity achievements/milestones — NO gossip)
4. Science and Space (ISRO and NASA missions, space discoveries, rocket launches, scientific breakthroughs, experiments)
5. Business and Startups (major startup developments, new products/services, business success stories, consumer tech/market news)
6. Education and Careers (verified job announcements, exam notifications, scholarships, opportunities, education updates)
7. Amazing and Unusual News (world records, wildlife discoveries, unusual inventions, inspiring achievements, natural phenomena)
8. Useful Public Information (major transport/infrastructure updates, weather alerts, consumer alerts, everyday public updates)

Collect up to 5–10 top fresh candidate stories across these categories (including international stories relevant or interesting to Indian audiences).

## B. STRICTLY PROHIBITED CONTENT (ZERO TOLERANCE)
REJECT any candidate that primarily involves OR has even a substantial secondary connection to controversy concerning:
- Indian or international politics, political parties, politicians, elections, political disputes, allegations, or protests.
- Religion, religious conflict, communal issues, communal violence, terrorism, or hate speech.
- Caste, ethnicity, race, linguistic conflicts, cultural disputes, controversial traditions, religious/cultural comparisons, or identity debates.
- Fake news, unverified viral claims, celebrity gossip, fabricated rumours, graphic violence, disturbing accidents, sensational crime, small crime, minor incident, or misleading clickbait.
IMPORTANT: Evaluate full context, not just keywords. If uncertain whether a story is appropriate, reject it. Never try to make controversial news acceptable by rewriting its headline.

## C. FRESHNESS & D. SOURCE CREDIBILITY
- Prioritize news published **today** (strictly within the last 24 hours with a real verified timestamp). Extend to 48 hours (`extended_48h_window: true`) ONLY if insufficient <24h quality stories exist; if a story is from yesterday or older than 24h, do NOT mark `extended_48h_window: false` unless it actually occurred within 24 hours. Do not treat an old republished event as breaking news. Never invent timestamps, URLs, statistics, or trend data.
- Prioritise Reuters, Associated Press, PTI, BBC, The Hindu, Indian Express, Hindustan Times, Times of India, NDTV, ESPNcricinfo, and official ISRO, NASA, ICC, BCCI announcements or reputable specialist publications.
- Corroborate important factual claims across at least two independent credible sources (or official primary sources for direct announcements/results). If unconfirmed/developing, mark is_developing_unconfirmed: true and REJECT.

## E. VIRAL POTENTIAL SCORING (OUT OF 100) & F. INSTAGRAM ENGAGEMENT THINKING
Score each clean, credible, non-duplicate candidate out of 100:
1. Audience interest and broad appeal (0-20)
2. Freshness and timeliness (0-20)
3. Relevance to Indian audiences (0-15)
4. Shareability — would an Indian Instagram user share/save it? (0-15)
5. Curiosity and surprise factor (0-10)
6. Practical usefulness (0-10)
7. Visual and storytelling potential (0-5)
8. Credibility and corroboration (0-5)
Rules: 85-100 Exceptional | 75-84 High | 65-74 Moderate | Below 65 REJECT.
Do not reward controversy, outrage, or sensationalism. Do not claim to know Instagram's internal ranking formula.

## DEDUPLICATION & SHORTLISTING
Strictly ensure the Shortlisted Candidate is NOT logically similar to any of the last 15 news posted below. Compare distinctive people, organizations, places, event names, and actions even if worded differently.
Last 15 news posted:
{recent_summaries_text}

Select the single highest-scoring eligible candidate (the Shortlisted Candidate) that satisfies the Instagram engagement questions (scroll-stopping curiosity, share-worthy, explainable in a short Reel, accurate hook, fresh, non-controversial).

## PUBLISH GATE DECISION
{cooldown_instructions}

## INSTAGRAM REEL BACKGROUND IMAGE PLAN GENERATOR (FOR APPROVED STORY)
Design **1 to 3 cohesive vertical editorial image plans (`image_plans`)** depending on the complexity and storytelling richness of the approved story (15–30 second Instagram Reel + 3-second channel outro):
- **1 image**: Simple single-event update or straightforward announcement.
- **2 images**: Contrast, before/after, announcement + public impact, or two distinct visual angles.
- **3 images**: Multi-step milestone, major breakthrough, or deep story told cleanly across 3 beats (Beat 1: Main headline hook; Beat 2: Key mechanism/breakthrough/detail; Beat 3: Real-world impact/what happens next).
All images share **"The Daily Chronicle"** brand aesthetic (9:16 vertical, 1080 × 1920 pixels, smartphone safe areas keeping text clear of Instagram's top bar, right engagement icons, and bottom caption/audio overlay):
- **Branding**: Exact channel name `"The Daily Chronicle"` near the top in consistent editorial masthead style (never misspelled/abbreviated), adapting accent colors to fit the story.
- **Headline (`headline_text`)**: 5–10 words in simple English an average Indian audience understands instantly; visually dominant, curious without misleading/exaggerating.
- **Short News Description (`short_news_description`)**: 15–35 words per image (20–35 words on Image 1) in simple, conversational English directly below the headline explaining that scene's verified facts and why it matters (never invent numbers/quotes/dates).
- **Background Image Concept (`background_image_prompt`)**: Each image plan must describe a **unique, distinct visual scene** directly aligned with that beat of the story using cinematic editorial photography, rich colors, depth, and clean negative space behind text:
  * Sports/Cricket: Dramatic stadium atmosphere, cricket gear, action energy — DO NOT fabricate identifiable players or specific match scoreboards.
  * Technology/AI: Futuristic tech settings, sleek devices, subtle clean digital elements.
  * Entertainment: Cinematic film/stage lighting and props — DO NOT fabricate identifiable celebrity faces.
  * Science/Space: Realistic space environments, rockets, astronomical visuals, scientific equipment.
  * Business/Startups: Modern offices, product-focused visuals, dynamic industry settings.
  * Education/Careers: Clean study/campus/books or career environments.
  * Wildlife/Amazing: Detailed nature photography, realistic animals, striking natural phenomena.
  * Useful Public Info / Automobiles / Infrastructure: Cinematic vehicles, rail/highway/weather/consumer scenes matching the story.
- **Accuracy & Separation**: Separate background visuals from typography completely! Never generate fake logos, fake news screenshots, identifiable faces/celebrities, controversial religious/political/ethnic imagery, scoreboards, signs, banners, digital readouts, or any text/letters/numbers/watermarks inside `background_image_prompt`. Keep `text_overlay_layout.text_position` aligned with `background_composition` (`"bottom"` when the subject is in upper/middle frame with clean lower third; `"top"` when the subject fills the lower half). Do NOT add "See description for more detail" in the plan or image.

## OUTPUT FORMAT
If eligible is true:
- category: Classify into exactly one of [{allowed_topics}].
- headline: Main Instagram headline matching `image_plans[0].headline_text` (5–10 words).
- highlight_words: 1 to 3 exact impactful words or short phrases copied verbatim from `headline` to highlight in accent color.
- image_body: Matching `image_plans[0].short_news_description` (20–35 words in simple conversational English explaining what happened and why it matters).
- background_image_prompt: Matching `image_plans[0].background_image_prompt`.
- image_plans: List of **1 to 3** structured scene objects depending on story depth. Each item contains: `scene_index`, `design_title`, `visual_concept`, `aspect_ratio` ("9:16"), `resolution` ("1080x1920"), `brand_name` ("The Daily Chronicle"), `background_image_prompt`, `headline_text`, `highlight_words`, `short_news_description`, `text_overlay_layout` (`"text_position"`: "bottom" or "top"), `headline_typography`, `description_typography`, `colour_palette` (`"accent_hex"`), `lighting_and_mood`, `background_composition`, `safe_area_instructions`, `negative_prompt`, `final_rendering_instructions`.
- description: Engaging factual Instagram caption (max 500 words):
  * Line 1: Compelling opening hook with relevant emoji.
  * Body: Clear short paragraphs explaining verified facts, India relevance, and practical/human context.
  * Comment prompt: 1 genuine, non-controversial question inviting discussion.
  * Must include "Like, share and follow" exactly once.
- hashtags: 5-15 relevant hashtags starting with # (unique, accurate topic tags).
- selected_story: factual title, 5-15 word event_summary, ISO 8601 published_at, url, and source.
- sources: list of independent credible source articles used.

If eligible is false:
- Return research_metrics, selected_story (best candidate or null), and publish_assessment with eligible: false and clear rejection reason. Do not generate headline, highlight_words, image_body, background_image_prompt, image_plans, description, or hashtags.

Return ONLY one valid JSON object (no markdown, no backticks, no extra text):
{{
  "research_metrics": {{"candidate_count": 8, "unique_candidate_count": 6}},
  "selected_story": {{
    "title": "Original credible headline of shortlisted candidate",
    "event_summary": "5-15 word factual summary of the event",
    "published_at": "2026-11-28T06:00:00Z",
    "url": "https://...",
    "source": "Publisher Name"
  }},
  "publish_assessment": {{
    "eligible": true,
    "prohibited_content_free": true,
    "is_developing_unconfirmed": false,
    "extended_48h_window": false,
    "viral_score": 84,
    "score_breakdown": {{
      "audience_interest": 17,
      "freshness": 18,
      "india_relevance": 13,
      "shareability": 13,
      "curiosity": 8,
      "usefulness": 7,
      "visual_potential": 4,
      "credibility": 4
    }},
    "corroborating_sources_count": 2,
    "confidence_level": "HIGH",
    "editorial_risk_flags": [],
    "instagram_algorithm_fit": true,
    "viral_shareability": "HIGH",
    "scroll_stop_hook": "HIGH",
    "mass_relatability": "HIGH",
    "comment_debate_potential": "HIGH",
    "recency": "VERY_HIGH",
    "is_routine_or_low_engagement": false,
    "cooldown_active": {str(posting_cooldown_active).lower()},
    "cooldown_exception": "NONE",
    "nationwide_impact": true,
    "is_minor_or_local_incident": false,
    "actual_event_at": "2026-11-28T06:00:00Z",
    "viral_rationale": "Why Indian Instagram users will stop scrolling, save, and share this non-controversial story",
    "reason": "Clear explanation of score, credibility, and publishing decision"
  }},
  "category": "Science and Space",
  "headline": "Punchy Accurate Instagram News Headline",
  "highlight_words": ["Punchy", "Headline"],
  "image_body": "20 to 35 crisp words in simple conversational English explaining what happened, verified facts, and why Indian viewers should care about this story.",
  "background_image_prompt": "Photorealistic editorial scene directly matching beat 1 with dramatic cinematic lighting, central focal subject framed for 9:16 vertical crop, clean negative space behind text, zero text or logos.",
  "image_plans": [
    {{
      "scene_index": 1,
      "design_title": "Beat 1 Hook Poster",
      "visual_concept": "Main dramatic visual introducing the verified breakthrough",
      "aspect_ratio": "9:16",
      "resolution": "1080x1920",
      "brand_name": "The Daily Chronicle",
      "background_image_prompt": "Photorealistic editorial scene directly matching beat 1 with dramatic cinematic lighting, central focal subject framed for 9:16 vertical crop, clean negative space, zero text or logos.",
      "headline_text": "Punchy Accurate Instagram News Headline",
      "highlight_words": ["Punchy", "Headline"],
      "short_news_description": "20 to 35 crisp words explaining the core verified milestone and why it matters right now.",
      "text_overlay_layout": {{"text_position": "bottom", "alignment": "left", "hierarchy": "masthead -> category -> headline -> description -> cta"}},
      "headline_typography": "ExtraBold geometric sans-serif, large dominant white with accent highlight pills",
      "description_typography": "Medium clean sans-serif, soft off-white, high line spacing",
      "colour_palette": {{"accent_hex": "#2563EB", "text_primary": "#FFFFFF", "text_secondary": "#EAEFF6", "overlay_tint": "#060A12"}},
      "lighting_and_mood": "Bright dramatic editorial lighting matching the story mood",
      "background_composition": "Subject centered in upper/middle frame with clean negative space in text area",
      "safe_area_instructions": "Keep text within left=68px, right=136px, top>=105px, bottom<=1575px clear of Reels UI",
      "negative_prompt": "text, letters, numbers, words, watermarks, logos, scoreboards, signs, blurry faces, distorted hands",
      "final_rendering_instructions": "Composite clean typographic overlay with gradient backdrop over background image"
    }},
    {{
      "scene_index": 2,
      "design_title": "Beat 2 Impact Poster",
      "visual_concept": "Visual showing real-world scale or practical impact for Indian audiences",
      "aspect_ratio": "9:16",
      "resolution": "1080x1920",
      "brand_name": "The Daily Chronicle",
      "background_image_prompt": "Distinct second photorealistic scene illustrating the practical impact and future outcome, rich lighting, zero text or logos.",
      "headline_text": "What This Milestone Means Next",
      "highlight_words": ["Milestone", "Next"],
      "short_news_description": "15 to 30 crisp words explaining the next steps, timeline, or everyday impact for Indians.",
      "text_overlay_layout": {{"text_position": "bottom", "alignment": "left", "hierarchy": "masthead -> category -> headline -> description -> cta"}},
      "headline_typography": "ExtraBold geometric sans-serif, large dominant white with accent highlight pills",
      "description_typography": "Medium clean sans-serif, soft off-white, high line spacing",
      "colour_palette": {{"accent_hex": "#2563EB", "text_primary": "#FFFFFF", "text_secondary": "#EAEFF6", "overlay_tint": "#060A12"}},
      "lighting_and_mood": "Clean forward-looking cinematic lighting",
      "background_composition": "Subject centered in upper/middle frame with uncluttered lower third",
      "safe_area_instructions": "Keep text within left=68px, right=136px, top>=105px, bottom<=1575px clear of Reels UI",
      "negative_prompt": "text, letters, numbers, words, watermarks, logos, scoreboards, signs, blurry faces, distorted hands",
      "final_rendering_instructions": "Composite clean typographic overlay with gradient backdrop over background image"
    }}
  ],
  "description": "🚀 Accurate scroll-stopping hook about the story!\\n\\nShort factual explanation with verified details and why it matters to India.\\n\\nWhat excites or surprises you most about this? Share your thoughts below!\\n\\nLike, share and follow",
  "hashtags": ["#IndiaNews", "#ScienceAndSpace", "#TechIndia", "#TrendingIndia", "#DidYouKnow"],
  "sources": [{{"name": "Publisher", "title": "Article Title", "url": "https://..."}}]
}}"""
