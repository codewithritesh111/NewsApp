# India News AI Studio

The pipeline uses Groq browser search to find and compare current news, Cloudflare Workers AI to generate one supporting editorial illustration, Pillow to create one vertical news image, and a bundled FFmpeg binary to turn that still into a 15-second Reel with category-matched BGM. The story search, comparison, and duplicate-event history remain in place. The pipeline no longer creates carousels or uses `Last Slide.jpg`.

## What the pipeline creates

1. Groq researches current, credible news relevant to Indian audiences, compares candidates, and selects one non-duplicate story. The rolling `shortlisted_headlines.json` history retains the latest 15 story summaries and is updated only after image and Reel generation succeed; text-only runs and failed media generation do not update it.
2. Groq assigns each selected story exactly one category: **Politics, Geopolitics, Crime, Technology, Business, Defence, Other, Government schemes, Social trends, Major Sad Event,** or **Major Good Event**. Its plan includes a concise image headline and explanation, a supporting illustration prompt, source metadata, and a description of no more than 500 words and 2,200 characters.
3. Cloudflare creates the illustration background. Pillow lays out the story headline, short explanation, “See description for more detail,” and “Like, share and follow” on a 1080×1920 image. The story category is used for BGM selection but is not printed on the image.
4. The still image is encoded as a 15-second, 30-fps H.264 MP4. The app searches `BGM` for an audio filename matching the selected category, ignoring case and spaces (for example, `Major Sad Event` matches `majorsadevent.mp3`). If no category match exists, it uses a supported audio file named `Other` such as `Other.m4a`. The selected audio loops or is trimmed to fill the Reel; the image does not pan or animate.
5. If Cloudinary is configured, the MP4 is uploaded to a public HTTPS URL for Instagram to fetch. Publishing is always an explicit opt-in.

Generated illustrations are editorial artwork, not documentary images. Review the selected story, source links, dates, and copy before publishing. The supported classification categories are **Politics, Geopolitics, Crime, Technology, Business, Defence, Other, Government schemes, Social trends, Major Sad Event,** and **Major Good Event**.

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

## Schedule publishing with GitHub Actions

The workflow at `.github/workflows/scheduled-instagram.yml` publishes at these IST times:

| India time (IST) | GitHub Actions cron (UTC) |
|---|---|
| 08:50 | `20 3 * * *` |
| 09:50 | `20 4 * * *` |
| 11:50 | `20 6 * * *` |
| 14:50 | `20 9 * * *` |
| 17:50 | `20 12 * * *` |
| 19:30 | `0 14 * * *` |
| 21:00 | `30 15 * * *` |

Add the project to a **private GitHub repository**, commit and push the workflow to its default branch, then add these repository Actions secrets under **Settings → Secrets and variables → Actions**:

- `GROQ_API_KEY` or `GROQ_API_KEYS`
- Either `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`, or matching comma-separated `CLOUDFLARE_ACCOUNT_IDS` and `CLOUDFLARE_API_TOKENS`
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, and `CLOUDINARY_API_SECRET`
- `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`, and `INSTAGRAM_GRAPH_API_VERSION` (if not using the default)

Optional repository variables under **Settings → Secrets and variables → Actions → Variables** are `GROQ_MODEL`, `INSTAGRAM_GRAPH_API_VERSION`, and `BRAND_NAME`. The workflow defaults the model to `openai/gpt-oss-20b`, Graph API version to `v27.0`, and brand to `The Daily Chronicle`.

Commit the `BGM` directory and `shortlisted_headlines.json` to the private repository as well. After each run, the workflow commits shortlist changes back to the default branch using its `GITHUB_TOKEN`; workflow permissions must allow contents write, and branch rules must permit that bot push. This preserves duplicate detection between fresh hosted runners. Generated `output/` files are ignored and are not committed.

GitHub schedule expressions use UTC. Scheduled workflows can start later than the requested minute during GitHub load, and only run from the default branch. **Run workflow** is available for a manual run, but it also publishes a public Reel. Private-repository Actions usage is subject to the included minutes for your GitHub plan and may incur charges if you exceed them. Each scheduled run publishes a public Reel.

## Output files

Each run contains `groq_response.txt`, `post_plan.json`, `run_summary.json`, and a `best_news_...` directory containing `post_plan.json`, `selected_story.json`, `caption.txt`, `news_image.jpg`, `reel.mp4`, and `backgrounds/background.jpg`. `run_summary.json` records the selected BGM filename. `shortlisted_headlines.json` stores the rolling duplicate-detection history.
