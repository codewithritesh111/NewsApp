# India News AI Studio

The pipeline uses Groq browser search to discover and rank trending Indian news for Instagram Reels, Cloudflare Workers AI to generate a vibrant photojournalistic background, Pillow to render a high-contrast safe-zone typography overlay with highlighted keywords, and a bundled FFmpeg binary to encode a 15-second animated Reel with category-matched, round-robin rotated BGM. The story search, comparison, and duplicate-event history remain in place. The pipeline no longer creates carousels or uses `Last Slide.jpg`.

## What the pipeline creates

1. Groq browser search discovers fresh Indian and India-relevant international news within 24 hours (48h fallback only when needed), strictly filters out political, religious, communal, caste, cultural controversy, celebrity gossip, and sensational crime, and scores candidates out of 100 on broad appeal, freshness, India relevance, shareability, curiosity, practical usefulness, visual storytelling, and multi-source credibility. Candidates scoring below 65 (or below 75 during the 3-hour cooldown), unconfirmed developing stories, or prohibited topics are rejected as `skipped_publish_gate`; image, caption, upload, and Instagram publishing are skipped and the workflow exits cleanly. Duplicate screening compares distinctive words against the latest 15 posted-story summaries.
2. Groq assigns each selected story one approved category (**Sports and Cricket, Technology and Artificial Intelligence, Entertainment, Science and Space, Business and Startups, Education and Careers, Amazing and Unusual News, Useful Public Information**) and creates **1 to 3 scene image plans** (`image_plans`) depending on the story's complexity. Each scene includes an accurate headline, `highlight_words` for bold accent bars, a concise plain-English description, a center-framed photorealistic background prompt, and colour/layout guidance.
3. Cloudflare generates all 1–3 background images (`steps=8`). Pillow creates `news_image*.jpg`, `reel_background*.jpg`, and transparent `reel_overlay*.png` using modern Poppins typography, keyword highlight bars, smooth gradient blending, and Instagram Reels safe-zone margins.
4. FFmpeg encodes a **15–30 second**, 30-fps H.264 MP4 combining alternating subtle camera motion across the 1–3 slides, a round-robin `xfade` transition selected from 12 editorial transitions, a 3-second channel outro clip from `EndClip/` with dedicated outro audio (`BGM/EndClip/News Outro Sting.mp3`), a full-duration bottom timeline bar spanning both the news and EndClip, and loudness-normalized category BGM (`BGM/<Category>/` with 4 curated tracks per category cycling round-robin via `bgm_rotation.json`).
5. If Cloudinary is configured, the MP4 is uploaded to a public HTTPS URL for Instagram to fetch. Publishing is always an explicit opt-in.

Generated illustrations are editorial artwork, not documentary images. Review the selected story, source links, dates, and copy before publishing. Primary editorial categories are **Sports and Cricket, Technology and Artificial Intelligence, Entertainment, Science and Space, Business and Startups, Education and Careers, Amazing and Unusual News,** and **Useful Public Information** (legacy category aliases remain supported in BGM routing).

## Windows setup

```powershell
cd D:\NewsAppWithoutRSS\india-news-ai-studio
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set `GROQ_MODEL=openai/gpt-oss-20b` and add credentials to `.env`. For Groq, configure one or more comma-separated keys in `GROQ_API_KEYS` or `GROQ_API_KEY`. For Cloudflare, configure matching comma-separated values in `CLOUDFLARE_ACCOUNT_IDS` and `CLOUDFLARE_API_TOKENS`, or use the singular variables for one credential. Values at matching positions are paired.

Groq and Cloudflare requests try configured credentials in round-robin order after a failure, at most once per credential for each request. If all fail, the pipeline reports an error. Add or remove values in `.env` and restart the app for changes to take effect. Do not commit `.env`.

Groq browser search is supported with `openai/gpt-oss-20b` or `openai/gpt-oss-120b`; availability and quota depend on the Groq account. If Groq returns HTTP 401 for every key, replace revoked, expired, or mistyped keys. HTTP 429 indicates rate limiting or exhausted quota and may affect keys in the same organization.

## Run

Text-only research and plan (does not generate or publish a Reel):

```powershell
python -m app.main --text-only
```

Generate the image and Reel locally; upload the video to Cloudinary if configured:

```powershell
python -m app.main
```

## Instagram configuration and publishing

Create a Cloudinary account and set its cloud name, API key, and API secret in `.env`. Find them in the [Cloudinary Console](https://console.cloudinary.com/) under **Settings → API Keys**. The app uses the authenticated Cloudinary upload API and stores the returned secure video URL in the plan and run summary. The secret stays in `.env`.

For Instagram publishing, use a professional Instagram account linked to a Facebook Page and configure `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`, and `INSTAGRAM_GRAPH_API_VERSION`. The Meta app and token need permissions for Instagram publishing, including `instagram_basic` and `instagram_content_publish`, plus the Page permissions required by the chosen login flow. The generated description is used as the Reel caption; the app does not create a comment.

To research a story, create its image and Reel, host the video, and publish the Reel in one command:

```powershell
python -m app.main --publish-instagram
```

This command requires working Cloudflare, Cloudinary, and Instagram settings. It publishes publicly. `python -m app.main` without the publish flag does not publish.

An already-generated Reel with a Cloudinary URL in its `post_plan.json` can be published with:

```powershell
python -m app.main --publish-instagram --post-dir "output\2026-11-12_19-55-26\best_news_a-current-india-story"
```

## Half-hourly news monitoring with GitHub Actions

The workflow at `.github/workflows/scheduled-instagram.yml` runs every 30 minutes from 08:00 through 20:00 IST inclusive. Its UTC cron entries are:

```cron
30 2-14 * * *
0 3-14 * * *
```

Together these run at 02:30 UTC, then every 30 minutes through 14:30 UTC (08:00–20:00 IST). Each run independently researches news; only stories passing the strict gate continue to Reel generation and the existing publishing workflow. A rejected story exits with status 0 and is recorded in `output/<run>/run_summary.json`; `groq_response.txt` contains the structured assessment and the logs include candidate counts, ratings, and the decision. Manual **Run workflow** is still available and follows the same gate.

Configure these repository Actions secrets under **Settings → Secrets and variables → Actions**:

- `GROQ_API_KEY` or `GROQ_API_KEYS`
- Either `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`, or matching comma-separated `CLOUDFLARE_ACCOUNT_IDS` and `CLOUDFLARE_API_TOKENS`
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, and `CLOUDINARY_API_SECRET`
- `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`, and `INSTAGRAM_GRAPH_API_VERSION` (if not using the default)

Optional repository variables under **Settings → Secrets and variables → Actions → Variables** are `GROQ_MODEL`, `INSTAGRAM_GRAPH_API_VERSION`, and `BRAND_NAME`. The workflow defaults the model to `openai/gpt-oss-20b`, Graph API version to `v27.0`, and brand to `The Daily Chronicle`.

Commit the `BGM` directory and `shortlisted_headlines.json` to the repository as well. After a successful publish, the workflow commits shortlist changes back to the default branch using its `GITHUB_TOKEN`; workflow permissions must allow contents write, and branch rules must permit that bot push. This preserves duplicate detection between fresh hosted runners. Generated `output/` files are ignored and are not committed.

GitHub schedule expressions use UTC. Scheduled workflows can start later than the requested minute during GitHub load, and only run from the default branch. Every eligible automated or manual run can publish a public Reel; manual dispatch is not a dry run.

## Output files

Each run contains `groq_response.txt`, `post_plan.json`, `run_summary.json`, and a `best_news_...` directory containing `post_plan.json`, `selected_story.json`, `caption.txt`, `news_image.jpg`, `reel.mp4`, and `backgrounds/background.jpg`. `run_summary.json` records the selected BGM filename. `shortlisted_headlines.json` stores the rolling duplicate-detection history.
