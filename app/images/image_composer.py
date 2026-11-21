from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import re


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


def _normalise_display_text(text: str) -> str:
    return (text or "").translate(_DISPLAY_TRANSLATION)


def _font(size: int, bold: bool = False):
    candidates = (
        ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "seguiemb.ttf", "calibrib.ttf"]
        if bold else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf", "calibri.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    image = image.convert("RGB")
    ratio = max(width / image.width, height / image.height)
    resized = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
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


def _split_line_into_highlight_chunks(line: str, highlight_words: list[str]) -> list[tuple[str, bool]]:
    """Split a line of text into alternating (text_chunk, is_highlighted) segments.
    Also handles multi-word highlights when broken across lines.
    """
    if not highlight_words or not line.strip():
        return [(line, False)]

    # Collect both full phrases and individual significant words (>=3 chars)
    terms = []
    for hw in highlight_words:
        hws = str(hw).strip()
        if not hws:
            continue
        terms.append(hws)
        # If multi-word, also add non-trivial words so line-breaks don't lose the highlight
        words = [w.strip() for w in hws.split() if len(w.strip()) >= 3]
        if len(words) > 1:
            terms.extend(words)

    # De-duplicate terms, prioritize longer terms first
    terms = sorted(list(set(terms)), key=lambda t: len(t), reverse=True)

    matches: list[tuple[int, int]] = []
    line_lower = line.lower()
    for term in terms:
        term_lower = term.lower()
        start = 0
        while True:
            idx = line_lower.find(term_lower, start)
            if idx == -1:
                break
            matches.append((idx, idx + len(term)))
            start = idx + max(1, len(term))

    if not matches:
        return [(line, False)]

    # Merge overlapping matches
    matches.sort(key=lambda m: (m[0], -m[1]))
    merged: list[tuple[int, int]] = []
    for start, end in matches:
        if not merged:
            merged.append((start, end))
        else:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

    # Build chunk list
    chunks: list[tuple[str, bool]] = []
    cursor = 0
    for m_start, m_end in merged:
        if m_start > cursor:
            chunks.append((line[cursor:m_start], False))
        chunks.append((line[m_start:m_end], True))
        cursor = m_end
    if cursor < len(line):
        chunks.append((line[cursor:], False))

    return chunks


def _draw_brand_header(draw: ImageDraw.ImageDraw, brand: str, x: int, y: int, is_light_bg: bool = False):
    """Draw a clean, minimalistic brand title without letter box logo."""
    brand_text = _normalise_display_text(brand.strip()) or "The Daily Chronicle"
    text_font = _font(30, True)

    text_color = (20, 24, 30, 255) if is_light_bg else (255, 255, 255, 250)
    stroke_color = (255, 255, 255, 120) if is_light_bg else (0, 0, 0, 160)

    draw.text(
        (x, y),
        brand_text,
        font=text_font,
        fill=text_color,
        stroke_width=1,
        stroke_fill=stroke_color,
    )


def compose_news_image(
    background_path: Path,
    output_path: Path,
    headline: str,
    body: str,
    brand: str,
    width: int = 1080,
    height: int = 1920,
    layout: dict | None = None,
) -> Path:
    """Compose a modern, minimalistic news image post without cartoonish panels."""
    if layout is None:
        layout = {}

    text_position = str(layout.get("text_position", "bottom")).lower().strip()
    if text_position not in {"top", "bottom"}:
        text_position = "bottom"

    raw_highlights = layout.get("highlight_words", [])
    if isinstance(raw_highlights, str):
        raw_highlights = [raw_highlights]
    highlight_words = [str(w).strip() for w in raw_highlights if str(w).strip()]

    # 1. Image preparation: natural colors and high clarity
    image = Image.open(background_path)
    image = _cover_crop(image, width, height)
    # Enhance quality: crisp sharpness and contrast
    image = ImageEnhance.Sharpness(image).enhance(1.15)
    image = ImageEnhance.Contrast(image).enhance(1.04)
    image = ImageEnhance.Brightness(image).enhance(0.96)
    image = ImageEnhance.Color(image).enhance(1.06)

    # 2. Analyze background brightness in the designated text region
    # To choose between black text (on white/light bg) or white text (on dark bg)
    if text_position == "top":
        sample_box = (0, 0, width, int(height * 0.45))
    else:
        sample_box = (0, int(height * 0.55), width, height)

    sample_crop = image.crop(sample_box).convert("L")
    sample_pixels = (
        list(sample_crop.get_flattened_data())
        if hasattr(sample_crop, "get_flattened_data")
        else list(sample_crop.getdata())
    )
    avg_region_brightness = sum(sample_pixels) / max(1, len(sample_pixels))
    is_light_bg = avg_region_brightness > 130

    # 3. Smooth directional gradient overlay matching background brightness
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    # Gradient color: white if light background, dark charcoal if dark background
    grad_rgb = (255, 255, 255) if is_light_bg else (5, 8, 15)

    if text_position == "top":
        # Gradient on top 48% fading down
        gradient_h = int(height * 0.48)
        for y in range(gradient_h):
            progress = y / gradient_h
            alpha = int(215 * ((1.0 - progress) ** 1.35))
            overlay_draw.line((0, y, width, y), fill=(*grad_rgb, alpha))
    else:
        # Gradient on bottom 52% fading up
        gradient_start_y = int(height * 0.48)
        for y in range(gradient_start_y, height):
            progress = (y - gradient_start_y) / (height - gradient_start_y)
            alpha = int(225 * (progress ** 1.35))
            overlay_draw.line((0, y, width, y), fill=(*grad_rgb, alpha))

    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)

    margin = 74
    max_text_width = width - 2 * margin

    # 4. Typography configuration
    headline = _normalise_display_text(headline.strip())
    body = _normalise_display_text(body.strip())

    headline_size = 72
    headline_font = _font(headline_size, bold=True)
    headline_lines = _wrap_by_pixels(draw, headline, headline_font, max_text_width)
    while len(headline_lines) > 4 and headline_size > 52:
        headline_size -= 4
        headline_font = _font(headline_size, bold=True)
        headline_lines = _wrap_by_pixels(draw, headline, headline_font, max_text_width)
    if len(headline_lines) > 4:
        headline_lines = headline_lines[:4]
        headline_lines[-1] = headline_lines[-1].rstrip(" .") + "…"

    body_size = 35
    body_font = _font(body_size, bold=False)
    body_lines = _wrap_by_pixels(draw, body, body_font, max_text_width)
    while len(body_lines) > 3 and body_size > 28:
        body_size -= 2
        body_font = _font(body_size, bold=False)
        body_lines = _wrap_by_pixels(draw, body, body_font, max_text_width)
    if len(body_lines) > 3:
        body_lines = body_lines[:3]
        body_lines[-1] = body_lines[-1].rstrip(" .,;:") + "…"

    headline_line_height = int(headline_font.size * 1.22)
    body_line_height = int(body_font.size * 1.36)

    total_content_height = (
        len(headline_lines) * headline_line_height
        + 24
        + len(body_lines) * body_line_height
    )

    # 5. Vertical placement based on adaptive text_position
    if text_position == "top":
        brand_y = 74
        headline_start_y = brand_y + 80
    else:
        brand_y = 74  # Keep brand recognizable at top-left
        headline_start_y = height - 200 - total_content_height

    # Draw Brand
    _draw_brand_header(draw, brand, margin, brand_y, is_light_bg=is_light_bg)

    # 6. Text colors based on background brightness
    if is_light_bg:
        # Black text on light/white background
        text_color = (18, 22, 28, 255)
        text_stroke = (255, 255, 255, 120)
        body_color = (48, 54, 62, 255)
        body_stroke = (255, 255, 255, 100)
        highlight_box_color = (165, 20, 30, 245)  # Deep maroon
    else:
        # White text on dark/black background
        text_color = (255, 255, 255, 255)
        text_stroke = (0, 0, 0, 160)
        body_color = (230, 235, 242, 255)
        body_stroke = (0, 0, 0, 150)
        highlight_box_color = (195, 22, 32, 245)  # Crimson red

    # 7. Render Headline with Keyword Highlighting
    curr_y = headline_start_y

    for line in headline_lines:
        chunks = _split_line_into_highlight_chunks(line, highlight_words)
        curr_x = margin

        for chunk_text, is_highlighted in chunks:
            cbbox = draw.textbbox((0, 0), chunk_text, font=headline_font)
            chunk_width = cbbox[2] - cbbox[0]

            if is_highlighted and chunk_text.strip():
                # Draw highlight box with subtle padding
                pad_x = 10
                pad_y = 4
                box_rect = (
                    curr_x - pad_x,
                    curr_y - pad_y,
                    curr_x + chunk_width + pad_x,
                    curr_y + headline_font.size + pad_y + 4,
                )
                draw.rounded_rectangle(box_rect, radius=6, fill=highlight_box_color)
                # Highlighted text inside box is always white bold
                draw.text(
                    (curr_x, curr_y),
                    chunk_text,
                    font=headline_font,
                    fill=(255, 255, 255, 255),
                )
            else:
                draw.text(
                    (curr_x, curr_y),
                    chunk_text,
                    font=headline_font,
                    fill=text_color,
                    stroke_width=1,
                    stroke_fill=text_stroke,
                )
            curr_x += chunk_width

        curr_y += headline_line_height

    # 8. Render Body Sub-description
    curr_y += 24
    for bline in body_lines:
        draw.text(
            (margin, curr_y),
            bline,
            font=body_font,
            fill=body_color,
            stroke_width=1,
            stroke_fill=body_stroke,
        )
        curr_y += body_line_height

    # 9. Subtle bottom swipe indicator
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)
    return output_path


def compose_news_overlay(
    output_path: Path,
    headline: str,
    body: str,
    brand: str,
    width: int = 1080,
    height: int = 1920,
    layout: dict | None = None,
    is_light_bg: bool = False,
) -> Path:
    """Compose a transparent 1080x1920 RGBA overlay for superimposing text over video."""
    if layout is None:
        layout = {}
    text_position = layout.get("text_position", "top").lower()
    highlight_words = [w.strip().upper() for w in layout.get("highlight_words", []) if w and isinstance(w, str)]

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    grad_rgb = (255, 255, 255) if is_light_bg else (5, 8, 15)
    if text_position == "top":
        gradient_h = int(height * 0.48)
        for y in range(gradient_h):
            progress = y / gradient_h
            alpha = int(225 * ((1.0 - progress) ** 1.35))
            overlay_draw.line((0, y, width, y), fill=(*grad_rgb, alpha))
    else:
        gradient_start_y = int(height * 0.48)
        for y in range(gradient_start_y, height):
            progress = (y - gradient_start_y) / (height - gradient_start_y)
            alpha = int(230 * (progress ** 1.35))
            overlay_draw.line((0, y, width, y), fill=(*grad_rgb, alpha))

    margin = 74
    max_text_width = width - 2 * margin

    headline = _normalise_display_text(headline.strip())
    body = _normalise_display_text(body.strip())

    headline_size = 72
    headline_font = _font(headline_size, bold=True)
    headline_lines = _wrap_by_pixels(overlay_draw, headline, headline_font, max_text_width)
    while len(headline_lines) > 4 and headline_size > 52:
        headline_size -= 4
        headline_font = _font(headline_size, bold=True)
        headline_lines = _wrap_by_pixels(overlay_draw, headline, headline_font, max_text_width)
    if len(headline_lines) > 4:
        headline_lines = headline_lines[:4]
        headline_lines[-1] = headline_lines[-1].rstrip(" .") + "…"

    body_size = 35
    body_font = _font(body_size, bold=False)
    body_lines = _wrap_by_pixels(overlay_draw, body, body_font, max_text_width)
    while len(body_lines) > 3 and body_size > 28:
        body_size -= 2
        body_font = _font(body_size, bold=False)
        body_lines = _wrap_by_pixels(overlay_draw, body, body_font, max_text_width)
    if len(body_lines) > 3:
        body_lines = body_lines[:3]
        body_lines[-1] = body_lines[-1].rstrip(" .,;:") + "…"

    headline_line_height = int(headline_font.size * 1.22)
    body_line_height = int(body_font.size * 1.36)
    total_content_height = (
        len(headline_lines) * headline_line_height
        + 24
        + len(body_lines) * body_line_height
    )

    if text_position == "top":
        brand_y = 74
        headline_start_y = brand_y + 80
    else:
        brand_y = 74
        headline_start_y = height - 200 - total_content_height

    _draw_brand_header(overlay_draw, brand, margin, brand_y, is_light_bg=is_light_bg)

    if is_light_bg:
        headline_color = (18, 22, 28)
        headline_stroke = (255, 255, 255, 120)
        body_color = (45, 55, 68)
        body_stroke = (255, 255, 255, 100)
    else:
        headline_color = (255, 255, 255)
        headline_stroke = (0, 0, 0, 160)
        body_color = (220, 225, 235)
        body_stroke = (0, 0, 0, 140)

    curr_y = headline_start_y
    for hline in headline_lines:
        chunks = _split_line_into_highlight_chunks(hline, highlight_words)
        curr_x = margin
        for text_part, is_highlight in chunks:
            cbbox = overlay_draw.textbbox((0, 0), text_part, font=headline_font)
            chunk_w = cbbox[2] - cbbox[0]
            if is_highlight and text_part.strip():
                badge_bg = (175, 20, 32, 235)
                badge_pad_x = 8
                badge_pad_y = 3
                box = [
                    curr_x - badge_pad_x,
                    curr_y - badge_pad_y,
                    curr_x + chunk_w + badge_pad_x,
                    curr_y + headline_font.size + badge_pad_y + 2,
                ]
                overlay_draw.rounded_rectangle(box, radius=6, fill=badge_bg)
                overlay_draw.text(
                    (curr_x, curr_y),
                    text_part,
                    font=headline_font,
                    fill=(255, 255, 255),
                    stroke_width=1,
                    stroke_fill=(80, 0, 0),
                )
            else:
                overlay_draw.text(
                    (curr_x, curr_y),
                    text_part,
                    font=headline_font,
                    fill=headline_color,
                    stroke_width=1,
                    stroke_fill=headline_stroke,
                )
            curr_x += chunk_w
        curr_y += headline_line_height

    curr_y += 24
    for bline in body_lines:
        overlay_draw.text(
            (margin, curr_y),
            bline,
            font=body_font,
            fill=body_color,
            stroke_width=1,
            stroke_fill=body_stroke,
        )
        curr_y += body_line_height

    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(output_path, "PNG")
    return output_path


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
