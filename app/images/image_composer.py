import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageEnhance


FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

_DISPLAY_TRANSLATION = str.maketrans({
    "\u00a0": " ",
    "\u00ad": "-",
    "\u2007": " ",
    "\u202f": " ",
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u2060": "",
})

_CATEGORY_ACCENTS: dict[str, tuple[int, int, int, int]] = {
    "sportsandcricket": (16, 163, 127, 245),
    "technologyandartificialintelligence": (37, 99, 235, 245),
    "entertainment": (217, 45, 114, 245),
    "scienceandspace": (59, 130, 246, 245),
    "businessandstartups": (37, 99, 235, 245),
    "educationandcareers": (217, 119, 6, 245),
    "amazingandunusualnews": (168, 85, 247, 245),
    "usefulpublicinformation": (225, 39, 49, 245),
    "breakingnews": (225, 39, 49, 245),
    "crime": (225, 39, 49, 245),
    "emergencyreporting": (225, 39, 49, 245),
    "disaster": (220, 48, 48, 245),
    "majorsadevent": (220, 48, 48, 245),
    "politics": (234, 88, 12, 245),
    "geopolitics": (234, 88, 12, 245),
    "defence": (234, 88, 12, 245),
    "governmentschemes": (234, 88, 12, 245),
    "business": (37, 99, 235, 245),
    "technology": (37, 99, 235, 245),
    "achievement": (16, 163, 127, 245),
    "majorgoodevent": (16, 163, 127, 245),
    "trends": (217, 45, 114, 245),
    "socialtrends": (217, 45, 114, 245),
}

_CATEGORY_LABELS: dict[str, str] = {
    "sportsandcricket": "SPORTS & CRICKET",
    "technologyandartificialintelligence": "TECH & AI",
    "entertainment": "ENTERTAINMENT",
    "scienceandspace": "SCIENCE & SPACE",
    "businessandstartups": "BUSINESS & STARTUPS",
    "educationandcareers": "EDUCATION & CAREERS",
    "amazingandunusualnews": "AMAZING & UNUSUAL",
    "usefulpublicinformation": "PUBLIC UPDATE",
    "breakingnews": "BREAKING NEWS",
    "crime": "CRIME ALERT",
    "emergencyreporting": "URGENT UPDATE",
    "disaster": "DISASTER ALERT",
    "majorsadevent": "MAJOR ALERT",
    "politics": "INDIA POLITICS",
    "geopolitics": "WORLD & INDIA",
    "defence": "DEFENCE",
    "governmentschemes": "POLICY UPDATE",
    "business": "BUSINESS & MARKETS",
    "technology": "TECH & AI",
    "achievement": "PROUD MOMENT",
    "majorgoodevent": "GOOD NEWS",
    "trends": "TRENDING NOW",
    "socialtrends": "VIRAL TREND",
    "other": "INDIA NEWS",
}

_STOP_HIGHLIGHT_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "has", "have", "he", "her", "his", "in", "into", "is", "it", "its", "of",
    "on", "or", "over", "she", "that", "the", "their", "they", "this", "to",
    "under", "was", "were", "what", "when", "where", "which", "who", "why", "will", "with",
}


def _normalise_display_text(text: str) -> str:
    return text.translate(_DISPLAY_TRANSLATION)


def _font(size: int, bold: bool = False, weight: str | None = None):
    if weight == "extrabold":
        candidates = [
            str(FONTS_DIR / "Poppins-ExtraBold.ttf"),
            str(FONTS_DIR / "Poppins-Bold.ttf"),
            "arialbd.ttf",
            "Arial Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
    elif weight == "semibold":
        candidates = [
            str(FONTS_DIR / "Poppins-SemiBold.ttf"),
            str(FONTS_DIR / "Poppins-Bold.ttf"),
            "arialbd.ttf",
            "Arial Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
    elif weight == "medium":
        candidates = [
            str(FONTS_DIR / "Poppins-Medium.ttf"),
            str(FONTS_DIR / "Poppins-Regular.ttf"),
            "arial.ttf",
            "Arial.ttf",
            "DejaVuSans.ttf",
        ]
    elif weight == "bold":
        candidates = [
            str(FONTS_DIR / "Poppins-Bold.ttf"),
            "arialbd.ttf",
            "Arial Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
    elif bold:
        candidates = [
            "arialbd.ttf",
            "Arial Bold.ttf",
            "DejaVuSans-Bold.ttf",
            str(FONTS_DIR / "Poppins-Bold.ttf"),
        ]
    else:
        candidates = [
            "arial.ttf",
            "Arial.ttf",
            "DejaVuSans.ttf",
            str(FONTS_DIR / "Poppins-Regular.ttf"),
        ]

    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    image = image.convert("RGB")
    ratio = max(width / image.width, height / image.height)
    resized = image.resize(
        (int(image.width * ratio), int(image.height * ratio)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _wrap_by_pixels(draw, text, font, max_width):
    words = (text or "").split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _parse_accent_hex(accent_hex: str | None) -> tuple[int, int, int, int] | None:
    if not isinstance(accent_hex, str):
        return None
    cleaned = accent_hex.strip().lstrip("#")
    if len(cleaned) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in cleaned):
        return None
    r = int(cleaned[0:2], 16)
    g = int(cleaned[2:4], 16)
    b = int(cleaned[4:6], 16)
    # Reject overly bright/pale colors that would reduce white-text contrast on pills
    relative_luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    if relative_luminance > 0.48 or relative_luminance < 0.04:
        return None
    return (r, g, b, 245)


def _category_accent(category: str) -> tuple[int, int, int, int]:
    key = (category or "").casefold().replace(" ", "")
    return _CATEGORY_ACCENTS.get(key, (225, 39, 49, 245))


def _category_label(category: str) -> str:
    key = (category or "").casefold().replace(" ", "")
    if key in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[key]
    cleaned = (category or "").strip()
    return cleaned.upper() if cleaned else "TRENDING NEWS"


def _resolve_highlight_phrases(headline: str, highlight_words: list[str] | None) -> list[str]:
    cleaned_phrases = [
        _normalise_display_text(item.strip())
        for item in (highlight_words or [])
        if isinstance(item, str) and item.strip()
    ]
    if cleaned_phrases:
        return cleaned_phrases[:3]

    # Automatic fallback: highlight numbers/amounts/acronyms or strongest content tokens
    tokens = re.findall(r"[₹$€£]?\d[\d,.\-/a-zA-Z%]*|[A-Z]{2,}|[^\W_]+", headline)
    priority: list[str] = []
    for token in tokens:
        if any(ch.isdigit() for ch in token) or (token.isupper() and len(token) >= 2):
            priority.append(token)
    if priority:
        return priority[:2]
    content_tokens = [
        token for token in tokens
        if len(token) >= 4 and token.casefold() not in _STOP_HIGHLIGHT_WORDS
    ]
    if content_tokens:
        content_tokens.sort(key=len, reverse=True)
        return [content_tokens[0]]
    return []


def _find_highlight_spans(
    line: str,
    phrases: list[str],
    next_line: str = "",
    prev_line: str = "",
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    lowered_line = line.casefold()
    for phrase in phrases:
        target = phrase.strip().casefold()
        if not target:
            continue
        matched = False
        pattern = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)", flags=re.IGNORECASE)
        for match in pattern.finditer(line):
            spans.append((match.start(), match.end()))
            matched = True
        if not matched and target in lowered_line:
            idx = lowered_line.find(target)
            if idx >= 0:
                spans.append((idx, idx + len(target)))
                matched = True

        # Handle multi-word phrases split across a line break (e.g. "... 120" / "km/h winds ...")
        if not matched and " " in target:
            words = target.split()
            for split_idx in range(1, len(words)):
                head = " ".join(words[:split_idx])
                tail = " ".join(words[split_idx:])
                if next_line:
                    head_match = re.search(rf"(?<!\w){re.escape(head)}\s*$", line, flags=re.IGNORECASE)
                    tail_match = re.match(rf"^\s*{re.escape(tail)}(?!\w)", next_line, flags=re.IGNORECASE)
                    if head_match and tail_match:
                        spans.append((head_match.start(), len(line.rstrip())))
                if prev_line:
                    head_match = re.search(rf"(?<!\w){re.escape(head)}\s*$", prev_line, flags=re.IGNORECASE)
                    tail_match = re.match(rf"^\s*{re.escape(tail)}(?!\w)", line, flags=re.IGNORECASE)
                    if head_match and tail_match:
                        spans.append((0, tail_match.end()))
    if not spans:
        return []
    spans.sort()
    merged: list[tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        prev_start, prev_end = merged[-1]
        gap = line[prev_end:start]
        if start <= prev_end or (gap and gap.isspace() and len(gap) <= 2):
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _draw_highlighted_line(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    line: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    highlight_phrases: list[str],
    accent_color: tuple[int, int, int, int],
    prev_line: str = "",
    next_line: str = "",
) -> None:
    spans = _find_highlight_spans(line, highlight_phrases, next_line=next_line, prev_line=prev_line)
    if not spans:
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 170))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        return

    metrics_bbox = draw.textbbox((0, 0), "Ag", font=font)
    top_pad = max(2, int(font.size * 0.04))
    bottom_pad = max(4, int(font.size * 0.08))
    glyph_top = metrics_bbox[1]
    glyph_bottom = metrics_bbox[3]

    # Draw accent pills first so text never gets clipped by adjacent spans
    for start, end in spans:
        prefix = line[:start]
        segment = line[start:end]
        seg_x1 = x + int(draw.textlength(prefix, font=font))
        seg_x2 = x + int(draw.textlength(prefix + segment, font=font))
        draw.rounded_rectangle(
            (seg_x1 - 8, y + glyph_top - top_pad, seg_x2 + 8, y + glyph_bottom + bottom_pad),
            radius=10,
            fill=accent_color,
        )

    draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 150))
    draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))


def compose_slide(background_path: Path, output_path: Path, slide: dict,
                  brand: str, index: int, total: int,
                  width: int = 1080, height: int = 1350):
    image = Image.open(background_path)
    image = _cover_crop(image, width, height)
    image = ImageEnhance.Brightness(image).enhance(0.53)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    for y in range(height):
        alpha = int(65 + 145 * (y / height))
        draw_overlay.line((0, y, width, y), fill=(4, 10, 22, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)

    margin = 76
    small = _font(30, True)
    title_font = _font(70 if index == 1 else 60, True)
    body_font = _font(33, False)
    footer_font = _font(25, True)

    if index == 1:
        brand_text = _normalise_display_text(brand.strip())
        brand_width = draw.textbbox((0, 0), brand_text, font=small)[2] + 44
        draw.rounded_rectangle(
            (margin, 54, margin + brand_width, 112),
            radius=18,
            fill=(210, 38, 48, 245),
        )
        draw.text((margin + 22, 66), brand_text, font=small, fill=(255, 255, 255, 255))

    title = _normalise_display_text(slide.get("headline", "").strip())
    title_lines = _wrap_by_pixels(draw, title, title_font, width - 2 * margin)
    while len(title_lines) > 3 and title_font.size > 48:
        title_font = _font(title_font.size - 4, True)
        title_lines = _wrap_by_pixels(draw, title, title_font, width - 2 * margin)
    if len(title_lines) > 3:
        title_lines = title_lines[:3]
        title_lines[-1] = title_lines[-1].rstrip(" .") + "…"

    body = _normalise_display_text(slide.get("body", "").strip())
    body_lines = _wrap_by_pixels(draw, body, body_font, width - 2 * margin)
    while len(body_lines) > 5 and body_font.size > 27:
        body_font = _font(body_font.size - 2, False)
        body_lines = _wrap_by_pixels(draw, body, body_font, width - 2 * margin)
    if len(body_lines) > 5:
        body_lines = body_lines[:5]
        body_lines[-1] = body_lines[-1].rstrip(" .,;:") + "…"

    fact_font = _font(29, True)
    key_fact = _normalise_display_text(slide.get("key_fact", "").strip())
    fact_lines = _wrap_by_pixels(draw, key_fact, fact_font, width - 2 * margin - 44)
    if len(fact_lines) > 2:
        fact_lines = fact_lines[:2]
        fact_lines[-1] = fact_lines[-1].rstrip(" .,;:") + "…"
    fact_height = max(68, 28 + len(fact_lines) * 36) if key_fact else 0

    title_line_height = int(title_font.size * 1.12)
    body_line_height = int(body_font.size * 1.3)
    content_height = len(title_lines) * title_line_height + 18
    content_height += len(body_lines) * body_line_height
    if key_fact:
        content_height += 22 + fact_height
    footer_y = height - 92
    panel_bottom = footer_y - 34
    panel_top = max(int(height * 0.28), panel_bottom - content_height - 78)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle((28, panel_top, width - 28, panel_bottom),
                         radius=28, fill=(5, 10, 20, 205))
    image = Image.alpha_composite(image, panel)
    draw = ImageDraw.Draw(image)

    y = panel_top + 42
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill="white", stroke_width=1, stroke_fill=(0,0,0,100))
        y += title_line_height

    y += 18
    for line in body_lines:
        draw.text((margin, y), line, font=body_font, fill=(238, 242, 247, 255))
        y += body_line_height

    if key_fact:
        y += 22
        draw.rounded_rectangle(
            (margin, y, width - margin, y + fact_height),
            radius=18,
            fill=(210, 38, 48, 245),
        )
        fact_y = y + (fact_height - len(fact_lines) * 36) // 2
        for line in fact_lines:
            draw.text((margin + 22, fact_y), line, font=fact_font, fill="white")
            fact_y += 36

    draw.line((margin, footer_y - 24, width - margin, footer_y - 24), fill=(255,255,255,130), width=2)
    draw.text((margin, footer_y), "READ • UNDERSTAND • STAY INFORMED", font=footer_font,
              fill=(235, 235, 235, 235))
    page_text = f"{index:02d} / {total:02d}"
    bbox = draw.textbbox((0, 0), page_text, font=footer_font)
    draw.text((width - margin - (bbox[2] - bbox[0]), footer_y), page_text,
              font=footer_font, fill=(255,255,255,255))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, "JPEG", quality=94, optimize=True)
    return output_path


def compose_news_image(
    background_path: Path,
    output_path: Path,
    headline: str,
    body: str,
    brand: str,
    width: int = 1080,
    height: int = 1920,
    *,
    category: str = "",
    highlight_words: list[str] | None = None,
    accent_hex: str | None = None,
    text_position: str = "bottom",
    overlay_output_path: Path | None = None,
    cropped_bg_output_path: Path | None = None,
):
    bg_cropped = _cover_crop(Image.open(background_path), width, height)
    bg_cropped = ImageEnhance.Brightness(bg_cropped).enhance(0.98)
    bg_cropped = ImageEnhance.Color(bg_cropped).enhance(1.12)
    bg_cropped = ImageEnhance.Contrast(bg_cropped).enhance(1.06)

    if cropped_bg_output_path is not None:
        cropped_bg_output_path.parent.mkdir(parents=True, exist_ok=True)
        bg_cropped.save(cropped_bg_output_path, "JPEG", quality=95, optimize=True)

    accent_color = _parse_accent_hex(accent_hex) or _category_accent(category)
    position = "top" if (text_position or "").strip().lower() == "top" else "bottom"
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if position == "top":
        # Rich top-to-middle gradient for top-anchored editorial layout
        top_end = int(height * 0.60)
        top_solid = int(height * 0.34)
        for y in range(top_end):
            if y < top_solid:
                alpha = 235
            else:
                t = 1.0 - (y - top_solid) / max(1, top_end - top_solid)
                alpha = int(235 * (t ** 1.45))
            draw.line((0, y, width, y), fill=(6, 10, 18, alpha))
        # Subtle bottom gradient to anchor CTA cleanly above Instagram UI
        bottom_start = int(height * 0.72)
        for y in range(bottom_start, height):
            t = (y - bottom_start) / max(1, height - bottom_start)
            alpha = int(215 * (t ** 1.3))
            draw.line((0, y, width, y), fill=(6, 10, 18, alpha))
    else:
        # Subtle top vignette for masthead clarity while keeping main subject bright
        top_fade_end = int(height * 0.13)
        for y in range(top_fade_end):
            alpha = int(125 * (1 - y / max(1, top_fade_end)) ** 1.4)
            draw.line((0, y, width, y), fill=(6, 10, 18, alpha))

        # Smooth editorial gradient across lower half (no boxed panel blocking artwork)
        grad_start = int(height * 0.40)
        grad_mid = int(height * 0.68)
        grad_full = int(height * 0.82)
        for y in range(grad_start, height):
            if y < grad_mid:
                t = (y - grad_start) / max(1, grad_mid - grad_start)
                alpha = int(205 * (t ** 1.55))
            elif y < grad_full:
                t = (y - grad_mid) / max(1, grad_full - grad_mid)
                alpha = int(205 + 42 * t)
            else:
                alpha = 247
            draw.line((0, y, width, y), fill=(6, 10, 18, alpha))

    left_margin = 68
    right_margin = 136  # Keep clear of Instagram Reels right-side engagement buttons
    max_text_width = max(320, width - left_margin - right_margin)
    bottom_safe_y = height - int(height * 0.18)  # Keep completely above Reels bottom caption/audio bar

    # Top-left clean news masthead
    brand_text = _normalise_display_text(brand.strip()) or "The Daily Chronicle"
    brand_font = _font(27, weight="bold")
    brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    brand_w = min(brand_bbox[2] - brand_bbox[0] + 44, width - 2 * left_margin)
    brand_y = int(height * 0.055)
    brand_h = 54
    draw.rounded_rectangle(
        (left_margin, brand_y, left_margin + brand_w, brand_y + brand_h),
        radius=14,
        fill=accent_color,
    )
    brand_text_h = brand_bbox[3] - brand_bbox[1]
    brand_text_y = brand_y + (brand_h - brand_text_h) // 2 - brand_bbox[1]
    draw.text((left_margin + 22, brand_text_y), brand_text, font=brand_font, fill=(255, 255, 255, 255))

    # Prepare headline and body within Instagram Reels safe zone
    headline_norm = _normalise_display_text(headline.strip())
    headline_font = _font(66, weight="extrabold")
    headline_lines = _wrap_by_pixels(draw, headline_norm, headline_font, max_text_width)
    while len(headline_lines) > 4 and headline_font.size > 48:
        headline_font = _font(headline_font.size - 3, weight="extrabold")
        headline_lines = _wrap_by_pixels(draw, headline_norm, headline_font, max_text_width)
    if len(headline_lines) > 4:
        raise ValueError("News image headline is too long to render clearly.")

    body_norm = _normalise_display_text(body.strip())
    body_font = _font(34, weight="medium")
    body_lines = _wrap_by_pixels(draw, body_norm, body_font, max_text_width)
    while len(body_lines) > 5 and body_font.size > 26:
        body_font = _font(body_font.size - 2, weight="medium")
        body_lines = _wrap_by_pixels(draw, body_norm, body_font, max_text_width)
    if len(body_lines) > 5:
        body_lines = body_lines[:5]
        body_lines[-1] = body_lines[-1].rstrip(" .,;:") + "…"

    eyebrow_font = _font(22, weight="bold")
    cta_font = _font(25, weight="semibold")

    headline_line_height = int(headline_font.size * 1.24)
    body_line_height = int(body_font.size * 1.38)
    eyebrow_height = 42
    gap_eyebrow_headline = 16
    gap_headline_body = 24
    gap_body_divider = 24
    divider_to_cta = 18
    cta_height = 36

    if position == "top":
        stack_top = brand_y + brand_h + 26
    else:
        stack_height = (
            eyebrow_height
            + gap_eyebrow_headline
            + len(headline_lines) * headline_line_height
            + gap_headline_body
            + len(body_lines) * body_line_height
            + gap_body_divider
            + divider_to_cta
            + cta_height
        )
        stack_top = max(int(height * 0.44), bottom_safe_y - stack_height)

    # Category / Trending Eyebrow Chip with subtle dark backdrop for high contrast
    eyebrow_text = _category_label(category)
    eb_bbox = draw.textbbox((0, 0), eyebrow_text, font=eyebrow_font)
    eb_w = eb_bbox[2] - eb_bbox[0] + 46
    draw.rounded_rectangle(
        (left_margin, stack_top, left_margin + eb_w, stack_top + eyebrow_height),
        radius=10,
        fill=(8, 12, 22, 185),
    )
    draw.rounded_rectangle(
        (left_margin + 12, stack_top + 10, left_margin + 19, stack_top + eyebrow_height - 10),
        radius=2,
        fill=accent_color,
    )
    eb_text_h = eb_bbox[3] - eb_bbox[1]
    eb_text_y = stack_top + (eyebrow_height - eb_text_h) // 2 - eb_bbox[1]
    draw.text(
        (left_margin + 28, eb_text_y),
        eyebrow_text,
        font=eyebrow_font,
        fill=(255, 224, 155, 255),
    )

    # Headline with highlighted key phrases
    highlight_phrases = _resolve_highlight_phrases(headline_norm, highlight_words)
    y = stack_top + eyebrow_height + gap_eyebrow_headline
    for idx, line in enumerate(headline_lines):
        _draw_highlighted_line(
            draw,
            left_margin,
            y,
            line,
            headline_font,
            highlight_phrases,
            accent_color,
            prev_line=headline_lines[idx - 1] if idx > 0 else "",
            next_line=headline_lines[idx + 1] if idx + 1 < len(headline_lines) else "",
        )
        y += headline_line_height

    # Clean, easy-to-scan summary copy
    y += gap_headline_body
    for line in body_lines:
        draw.text((left_margin + 1, y + 2), line, font=body_font, fill=(0, 0, 0, 150))
        draw.text((left_margin, y), line, font=body_font, fill=(234, 239, 246, 255))
        y += body_line_height

    # Safe-zone divider & Engagement CTA
    if position == "top":
        cta_y = bottom_safe_y - cta_height
        div_y = cta_y - divider_to_cta
    else:
        div_y = y + gap_body_divider
        cta_y = div_y + divider_to_cta

    draw.rounded_rectangle(
        (left_margin, div_y, left_margin + 150, div_y + 4),
        radius=2,
        fill=accent_color,
    )
    cta_text = "Read full story in caption   •   Like, Share & Follow"
    draw.text((left_margin + 1, cta_y + 2), cta_text, font=cta_font, fill=(0, 0, 0, 150))
    draw.text((left_margin, cta_y), cta_text, font=cta_font, fill=(255, 224, 150, 255))

    if overlay_output_path is not None:
        overlay_output_path.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(overlay_output_path, "PNG")

    composed = Image.alpha_composite(bg_cropped.convert("RGBA"), overlay)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)
    return output_path
