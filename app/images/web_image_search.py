"""Download real photojournalism and camera-captured photos from the web for news posts."""

from __future__ import annotations

import logging
import re
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus

import requests
from PIL import Image, ImageEnhance

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

IMAGE_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

DISALLOWED_DOMAINS = {
    "britannica.com",
    "pixabay.com",
    "earth.com",
    "nasa.gov",
    "wallpapercave.com",
    "wallpaperaccess.com",
    "wallpapersden.com",
    "pngtree.com",
    "freepik.com",
    "vecteezy.com",
    "cleanpng.com",
    "pngwing.com",
    "clipartmax.com",
    "vectorstock.com",
    "etsystatic.com",
    "123rf.com",
    "istockphoto.com",
    "shutterstock.com",
    "alamy.com",
    "gettyimages.com",
}

DISALLOWED_URL_KEYWORDS = (
    "satellite",
    "space-station",
    "orbit-view",
    "clipart",
    "vector",
    "illustration",
    "drawing",
    "infographic",
    "diagram",
    "wallpaper",
    "cartoon",
    "sketch",
    "icon-",
    "logo-",
)


def extract_og_image_from_html(html_text: str) -> str | None:
    """Extract editorial photograph URL from article meta tags (og:image, twitter:image)."""
    if not html_text:
        return None
    patterns = [
        r'<meta[^>]*property=[\x27\x22]og:image[\x27\x22][^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22]',
        r'<meta[^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22][^>]*property=[\x27\x22]og:image[\x27\x22]',
        r'<meta[^>]*property=[\x27\x22]og:image:secure_url[\x27\x22][^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22]',
        r'<meta[^>]*name=[\x27\x22]twitter:image[\x27\x22][^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22]',
        r'<meta[^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22][^>]*name=[\x27\x22]twitter:image[\x27\x22]',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I)
        if match:
            url = match.group(1).strip()
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith(("http://", "https://")):
                return url
    return None


def fetch_article_og_image(url: str, timeout: int = 8) -> str | None:
    """Fetch article page and extract its main editorial press photo."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.ok:
            return extract_og_image_from_html(resp.text)
    except Exception as exc:
        log.warning("[IMAGE SEARCH] Failed to fetch article at %s: %s", url, exc)
    return None


def _clean_search_query(query: str) -> str:
    """Strip out meta-words like 9:16, portrait, wallpaper, vector, etc. to get core keywords."""
    cleaned = re.sub(
        r"(?i)\b(portrait|9:16|9/16|vertical|photo|photograph|wallpaper|vector|illustration|clipart|png|drawing)\b",
        "",
        query,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or query.strip()


def extract_video_from_html(html_text: str) -> str | None:
    """Extract direct news video URL from article meta tags or HTML5 video tags."""
    if not html_text:
        return None
    patterns = [
        r'<meta[^>]*property=[\x27\x22]og:video(?::secure_url|:url)?[\x27\x22][^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22]',
        r'<meta[^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22][^>]*property=[\x27\x22]og:video(?::secure_url|:url)?[\x27\x22]',
        r'<meta[^>]*name=[\x27\x22]twitter:player:stream[\x27\x22][^>]*content=[\x27\x22]([^\x27\x22]+)[\x27\x22]',
        r'<source[^>]+src=[\x27\x22](https?://[^\x27\x22]+\.(?:mp4|m3u8)[^\x27\x22]*)[\x27\x22]',
        r'<video[^>]+src=[\x27\x22](https?://[^\x27\x22]+\.(?:mp4|m3u8)[^\x27\x22]*)[\x27\x22]',
        r'[\x27\x22]contentUrl[\x27\x22]\s*:\s*[\x27\x22](https?://[^\x27\x22]+\.(?:mp4|m3u8)[^\x27\x22]*)[\x27\x22]',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.I)
        if match:
            url = match.group(1).strip()
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith(("http://", "https://")):
                return url

    # Broad fallback: look for high-res news mp4 video streams embedded in scripts
    broad_mp4s = re.findall(r'[\x27\x22](https?://[^\x27\x22]+\.(?:mp4|m3u8))[\x27\x22]', html_text)
    for u in broad_mp4s:
        u_low = u.lower()
        if not any(bad in u_low for bad in ("preview", "tooltip", "advert", "promo", "ad-", "_ad")):
            return u

    return None


def fetch_article_video(url: str, timeout: int = 8) -> str | None:
    """Fetch article page and extract news video URL if present."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.ok:
            return extract_video_from_html(resp.text)
    except Exception as exc:
        log.warning("[VIDEO SEARCH] Failed to fetch article for video at %s: %s", url, exc)
    return None


def _decode_bing_url(url: str) -> str:
    """Decode real destination URL from Bing click-tracking redirect link (u=a1<base64>)."""
    if "u=a1" in url:
        m = re.search(r"u=a1([A-Za-z0-9_-]+)", url)
        if m:
            b64_str = m.group(1)
            b64_str += "=" * (-len(b64_str) % 4)
            try:
                import base64
                decoded = base64.urlsafe_b64decode(b64_str).decode("utf-8", errors="ignore")
                if decoded.startswith(("http://", "https://")):
                    return decoded
            except Exception:
                pass
    return url


def probe_video_url(url: str, timeout: int = 10) -> tuple[float | None, int | None, int | None]:
    """Probe remote video URL with ffmpeg and return (duration, width, height)."""
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-hide_banner", "-i", url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = res.stderr or ""
        duration = None
        m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
        if m_dur:
            h, m, s = m_dur.groups()
            duration = int(h) * 3600 + int(m) * 60 + float(s)

        width, height = None, None
        m_dim = re.search(r"Stream\s*#\d+:\d+.*Video:.*?(\d{2,5})x(\d{2,5})", out)
        if m_dim:
            width, height = int(m_dim.group(1)), int(m_dim.group(2))
        return duration, width, height
    except Exception:
        return None, None, None


def download_video(
    url: str,
    output_path: Path,
    timeout: int = 40,
    min_duration: float = 20.0,
    max_duration: float = 60.0,
) -> Path:
    """Download video stream or mp4 from URL using ffmpeg or streaming HTTP.
    
    Strictly verifies that video duration is between min_duration and max_duration.
    """
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid video URL: {url!r}")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Fast probe to reject clearly out-of-bounds videos before heavy download
    probed_dur, _, _ = probe_video_url(url, timeout=6)
    if probed_dur is not None and (probed_dur < min_duration or probed_dur > max_duration):
        raise ValueError(
            f"Probed video duration {probed_dur:.1f}s is outside required [{min_duration}s, {max_duration}s] range."
        )

    downloaded = False
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        command = [
            ffmpeg_exe,
            "-y",
            "-i", url,
            "-t", str(int(max_duration + 5)),
            "-c", "copy",
            str(output_path.resolve()),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 50000:
            log.info("[VIDEO SEARCH] Video downloaded successfully via FFmpeg: %s (%d bytes)", output_path, output_path.stat().st_size)
            downloaded = True
    except Exception as exc:
        log.warning("[VIDEO SEARCH] FFmpeg direct stream download failed: %s; trying requests stream.", exc)

    if not downloaded:
        # HTTP streaming fallback
        resp = requests.get(url, headers=IMAGE_HEADERS, timeout=timeout, stream=True)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

    if not output_path.is_file() or output_path.stat().st_size < 50000:
        output_path.unlink(missing_ok=True)
        raise ValueError(f"Downloaded video file is too small or invalid: {url}")

    # Inspect downloaded file to strictly verify duration [20s, 60s]
    from app.images.reel_encoder import inspect_video
    duration, _ = inspect_video(output_path)
    if duration < min_duration or duration > max_duration:
        output_path.unlink(missing_ok=True)
        raise ValueError(
            f"Downloaded video duration {duration:.1f}s is outside required [{min_duration}s, {max_duration}s] range."
        )

    return output_path


def search_news_videos(query: str, limit: int = 8) -> list[str]:
    """Search internet for videos matching the news title, collect video links, and extract stream URLs."""
    clean_query = _clean_search_query(query)
    if not clean_query:
        return []
    # Search the internet for 9:16 vertical video for the news title
    encoded = quote_plus(f"{clean_query} 9:16 video")
    search_url = f"https://www.bing.com/videos/search?q={encoded}"
    try:
        resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=10)
        if not resp.ok:
            fallback_url = f"https://www.bing.com/news/search?q={quote_plus(f'{clean_query} video')}"
            resp = requests.get(fallback_url, headers=DEFAULT_HEADERS, timeout=10)
            if not resp.ok:
                return []
        links = re.findall(r'<a[^>]+href=[\x27\x22](https?://[^\x27\x22]+)[\x27\x22]', resp.text)
        links.extend(re.findall(r'data-url=[\x27\x22](https?://[^\x27\x22]+)[\x27\x22]', resp.text))

        video_page_links: list[str] = []
        for raw_link in links:
            link = _decode_bing_url(raw_link)
            l_low = link.lower()
            if any(ign in l_low for ign in ("bing.com", "microsoft.com", "msn.com", "google.com", "youtube.com")):
                continue
            # Search open internet video pages, clips, reels, shorts
            if any(k in l_low for k in ("video", "watch", "clip", "media", "show", "programme", "reel", "shorts")):
                if link not in video_page_links:
                    video_page_links.append(link)

        video_stream_urls: list[str] = []
        for v_page in video_page_links[:limit]:
            v_stream = fetch_article_video(v_page)
            if v_stream and v_stream not in video_stream_urls:
                video_stream_urls.append(v_stream)
        return video_stream_urls
    except Exception as exc:
        log.warning("[VIDEO SEARCH] Internet video search failed: %s", exc)
        return []


def fetch_news_video(
    video_url: str | None = None,
    article_url: str | None = None,
    sources_urls: list[str] | None = None,
    output_path: Path = Path("output/news_video.mp4"),
    search_query: str | None = None,
    news_title: str | None = None,
) -> Path | None:
    """Attempt to find and download a 9:16 video matching the news title from the internet.
    
    Strictly enforces video duration between 20 seconds and 1 minute (60 seconds).
    If no video meeting the duration requirement is found, returns None.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    query = (news_title or search_query or "").strip()

    # 1. Direct video URL from plan if provided
    if video_url and video_url.strip():
        try:
            log.info("[VIDEO SEARCH] Attempting direct video_url: %s", video_url)
            dl = download_video(video_url.strip(), output_path)
            from app.images.reel_encoder import inspect_video
            dur, _ = inspect_video(dl)
            if 20.0 <= dur <= 60.0:
                log.info("[VIDEO SEARCH] Direct video validated: duration=%.1fs", dur)
                return dl
            else:
                log.warning("[VIDEO SEARCH] Direct video duration %.1fs is outside [20s, 60s]; rejecting.", dur)
                dl.unlink(missing_ok=True)
        except Exception as exc:
            log.warning("[VIDEO SEARCH] Direct video URL download failed: %s", exc)

    # 2. Search open internet for 9:16 videos matching the news title
    if query:
        log.info("[VIDEO SEARCH] Searching the internet for 9:16 news video matching %r...", query)
        discovered_streams = search_news_videos(query, limit=6)
        for stream_url in discovered_streams:
            try:
                log.info("[VIDEO SEARCH] Trying discovered video stream from internet: %s", stream_url)
                dl = download_video(stream_url, output_path)
                from app.images.reel_encoder import inspect_video
                dur, _ = inspect_video(dl)
                if 20.0 <= dur <= 60.0:
                    log.info("[VIDEO SEARCH] Validated internet video (duration=%.1fs): %s", dur, dl)
                    return dl
                else:
                    log.warning("[VIDEO SEARCH] Video duration %.1fs is outside [20s, 60s]; rejecting.", dur)
                    dl.unlink(missing_ok=True)
            except Exception as exc:
                log.warning("[VIDEO SEARCH] Discovered stream download/validation failed: %s", exc)

    return None


def search_news_editorial_photos(query: str, limit: int = 6) -> list[str]:
    """Search Bing News for the story, collect top news article links, and extract their photos."""
    encoded = quote_plus(query)
    search_url = f"https://www.bing.com/news/search?q={encoded}"
    try:
        resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=10)
        if not resp.ok:
            return []
        article_links = re.findall(
            r'<a[^>]+class=[\x27\x22]title[\x27\x22][^>]+href=[\x27\x22](http[^\x27\x22]+)[\x27\x22]',
            resp.text,
        )
        photos: list[str] = []
        for link in article_links[:limit]:
            img_url = fetch_article_og_image(link)
            if img_url and img_url not in photos:
                photos.append(img_url)
        return photos
    except Exception as exc:
        log.warning("[IMAGE SEARCH] News editorial photo search failed: %s", exc)
        return []


def is_photographic(img: Image.Image) -> bool:
    """Validate that an image is a genuine photograph, not transparent clipart, line drawing, or sketch."""
    # 1. Reject if transparent alpha channel (cutouts / clipart)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        alpha = img.convert("RGBA").split()[-1]
        alpha_colors = alpha.getcolors(maxcolors=256)
        if alpha_colors:
            transparent_count = sum(count for count, val in alpha_colors if val < 128)
            total = img.width * img.height
            if transparent_count / max(1, total) > 0.12:
                return False

    # 2. Check natural color diversity (real photos have thousands of subtle color shades)
    rgb = img.convert("RGB")
    small = rgb.resize((100, 100), Image.Resampling.BOX)
    colors = small.getcolors(maxcolors=10000)
    unique_colors = len(colors) if colors is not None else 10000
    # Line drawings or flat clipart typically have < 200 unique colors in a 100x100 thumbnail
    # Real photos have 1500+ unique colors
    return unique_colors > 250


def download_image(
    url: str,
    output_path: Path,
    min_dimension: int = 400,
    timeout: int = 25,
    require_portrait: bool = False,
    require_photo: bool = True,
) -> Path:
    """Download an image from a direct URL and validate that it is a real camera photo."""
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid image URL: {url!r}")

    url_lower = url.lower()
    # Check domain blacklist
    if any(domain in url_lower for domain in DISALLOWED_DOMAINS):
        raise ValueError(f"URL domain is a known clipart/vector/stock provider: {url}")

    # Check keyword blacklist (satellite, space station, clipart, etc.)
    if any(keyword in url_lower for keyword in DISALLOWED_URL_KEYWORDS):
        raise ValueError(f"URL contains disallowed non-camera keyword: {url}")

    log.info("[IMAGE SEARCH] Downloading image from %s", url)
    try:
        resp = requests.get(
            url,
            headers=IMAGE_HEADERS,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"Failed to download image from {url}: {exc}") from exc

    content = resp.content
    if len(content) < 6000:
        raise ValueError(f"Downloaded image is suspiciously small ({len(content)} bytes)")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(BytesIO(content)) as raw_img:
            raw_img.verify()

        with Image.open(BytesIO(content)) as img:
            w, h = img.size
            if w < min_dimension and h < min_dimension:
                raise ValueError(f"Image dimension {w}x{h} is smaller than required {min_dimension}px")

            aspect_ratio = h / max(1, w)
            if require_portrait and aspect_ratio < 0.95:
                raise ValueError(f"Image is landscape ({w}x{h}, aspect={aspect_ratio:.2f}); portrait required")

            if require_photo and not is_photographic(img):
                raise ValueError("Image appears to be clipart, vector graphic, or transparent line art, not a photograph")

            img = img.convert("RGB")
            img = ImageEnhance.Sharpness(img).enhance(1.15)
            img = ImageEnhance.Contrast(img).enhance(1.04)
            img.save(output_path, "JPEG", quality=95)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise ValueError(f"Downloaded file from {url} failed validation: {exc}") from exc

    log.info("[IMAGE SEARCH] Image saved to %s (%dx%d, aspect=%.2f)", output_path, w, h, h / max(1, w))
    return output_path


def search_bing_images(query: str, limit: int = 12, aspect_tall: bool = False) -> list[str]:
    """Search Bing Images HTML for direct media URLs."""
    encoded_query = quote_plus(query)
    filter_param = "&qft=+filterui:aspect-tall" if aspect_tall else ""
    search_url = f"https://www.bing.com/images/search?q={encoded_query}&form=HDRSC2&first=1{filter_param}"
    try:
        response = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=12)
        if not response.ok:
            return []
        murls = re.findall(r'murl&quot;:&quot;(http[^&]+)&quot;', response.text)
        if not murls:
            murls = re.findall(r'"murl":"(http[^"]+)"', response.text)

        clean_urls = []
        for u in murls:
            u_low = u.lower()
            if not any(d in u_low for d in DISALLOWED_DOMAINS) and not any(k in u_low for k in DISALLOWED_URL_KEYWORDS):
                clean_urls.append(u)
        return clean_urls[:limit]
    except Exception as exc:
        log.warning("[IMAGE SEARCH] Bing image search failed: %s", exc)
        return []


def search_wikimedia_images(query: str, limit: int = 5) -> list[str]:
    """Search Wikimedia Commons for images related to query."""
    encoded_query = quote_plus(query)
    url = (
        f"https://commons.wikimedia.org/w/api.php?action=query&generator=search"
        f"&gsrsearch={encoded_query}&gsrnamespace=6&gsrlimit={limit}"
        f"&prop=imageinfo&iiprop=url|size|mime&format=json"
    )
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        if not response.ok:
            return []
        data = response.json()
        pages = data.get("query", {}).get("pages", {})
        urls = []
        for pid, page in pages.items():
            infos = page.get("imageinfo", [])
            for info in infos:
                img_url = info.get("url")
                mime = info.get("mime", "")
                if img_url and ("jpeg" in mime or "jpg" in img_url.lower()):
                    u_low = img_url.lower()
                    if not any(k in u_low for k in DISALLOWED_URL_KEYWORDS):
                        urls.append(img_url)
        return urls[:limit]
    except Exception as exc:
        log.warning("[IMAGE SEARCH] Wikimedia search failed: %s", exc)
        return []


def search_pexels(
    query: str,
    api_key: str,
    output_path: Path,
    orientation: str = "portrait",
) -> Path:
    """Search the Pexels API in portrait orientation and download the first matching photo."""
    log.info("[IMAGE SEARCH] Searching Pexels for '%s' (orientation=%s)", query, orientation)
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key, **DEFAULT_HEADERS},
            params={
                "query": query,
                "per_page": 5,
                "orientation": orientation,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"Pexels API request failed: {exc}") from exc

    data = resp.json()
    photos = data.get("photos", [])
    if not photos:
        raise ValueError(f"No Pexels results for query '{query}'")

    image_url = photos[0]["src"]["original"]
    return download_image(image_url, output_path, require_portrait=False)


def fetch_news_background(
    image_url: str | None,
    search_query: str,
    output_path: Path,
    article_url: str | None = None,
    sources_urls: list[str] | None = None,
    pexels_api_key: str | None = None,
    news_title: str | None = None,
) -> Path:
    """Download a real camera-captured 9:16 background image for a news post from the internet.

    Order of priority:
    1. Direct image_url if provided.
    2. Search the internet for 9:16 vertical photos matching the news title.
    3. Pexels portrait photo matching the news title (if API key configured).
    4. Standard image search on the internet (cover-cropped to 9:16 by image_composer).
    5. Wikimedia Commons on the internet.
    6. Fallback to news channel article photo if open internet search yields nothing.
    """
    errors: list[str] = []

    # 1. Direct URL if provided
    if image_url and image_url.strip():
        try:
            return download_image(image_url.strip(), output_path, require_portrait=False, require_photo=True)
        except Exception as exc:
            log.warning("[IMAGE SEARCH] Direct URL failed: %s", exc)
            errors.append(f"Direct URL: {exc}")

    # Determine query based on news title
    title_text = (news_title or search_query or "").strip()
    clean_query = _clean_search_query(title_text)
    if not clean_query:
        summary = "; ".join(errors) if errors else "No image_url, search_query, or news_title provided"
        raise ValueError(f"Could not obtain a background image. {summary}")

    words = clean_query.split()
    core_query = " ".join(words[:4]) if len(words) > 4 else clean_query

    # 2. Search open internet for 9:16 vertical portrait images for the news title
    log.info("[IMAGE SEARCH] Querying open internet for 9:16 ratio photos matching news title: %r (core: %r)", clean_query, core_query)
    candidate_urls: list[str] = []
    # Queries targeting 9:16 ratio and vertical portrait across the internet
    candidate_urls.extend(search_bing_images(f"{clean_query} 9:16 photo", limit=10, aspect_tall=True))
    candidate_urls.extend(search_bing_images(f"{clean_query} vertical photo", limit=8, aspect_tall=True))
    candidate_urls.extend(search_bing_images(f"{clean_query} photo", limit=8, aspect_tall=True))
    if core_query != clean_query:
        candidate_urls.extend(search_bing_images(f"{core_query} photo", limit=6, aspect_tall=True))

    for url in candidate_urls:
        try:
            return download_image(url, output_path, require_portrait=True, require_photo=True)
        except Exception as exc:
            errors.append(f"Tall URL {url}: {exc}")

    # 3. Fallback to Pexels portrait if API key provided
    if pexels_api_key:
        try:
            return search_pexels(core_query, pexels_api_key, output_path, orientation="portrait")
        except ValueError as exc:
            errors.append(f"Pexels: {exc}")

    # 4. Standard image search on open internet (will be cover-cropped to 9:16 by image_composer)
    photo_query = f"{clean_query} camera photo -satellite -diagram -space -clipart -vector"
    standard_urls: list[str] = []
    standard_urls.extend(search_bing_images(photo_query, limit=12, aspect_tall=False))
    if core_query != clean_query:
        standard_urls.extend(search_bing_images(f"{core_query} camera photo", limit=10, aspect_tall=False))
    standard_urls.extend(search_wikimedia_images(core_query, limit=5))

    for url in standard_urls:
        try:
            return download_image(url, output_path, require_portrait=False, require_photo=True)
        except Exception as exc:
            errors.append(f"Standard URL {url}: {exc}")

    # 5. Fallback to news channel article photos if internet search returns nothing
    channel_urls = []
    if article_url and article_url.strip():
        channel_urls.append(article_url.strip())
    if sources_urls:
        for u in sources_urls:
            if u and u.strip() and u.strip() not in channel_urls:
                channel_urls.append(u.strip())

    for ch_url in channel_urls:
        og_url = fetch_article_og_image(ch_url)
        if og_url:
            try:
                log.info("[IMAGE SEARCH] Fallback to channel photo (%s): %s", ch_url, og_url)
                return download_image(og_url, output_path, require_portrait=False, require_photo=True)
            except Exception as exc:
                errors.append(f"Channel photo ({ch_url}): {exc}")

    summary = "; ".join(errors[-4:]) if errors else "No images found"
    raise ValueError(f"Could not obtain a 9:16 background image for query {clean_query!r}. {summary}")
