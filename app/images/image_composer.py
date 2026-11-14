from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageEnhance


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
    return text.translate(_DISPLAY_TRANSLATION)

def _font(size: int, bold: bool = False):
    candidates = (
        ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"]
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
):
    image = _cover_crop(Image.open(background_path), width, height)
    image = ImageEnhance.Brightness(image).enhance(0.55)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(height):
        progress = y / height
        alpha = int(25 + 155 * progress**1.7)
        overlay_draw.line((0, y, width, y), fill=(4, 10, 22, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)

    margin = 78
    headline_font = _font(76, True)
    body_font = _font(38, False)
    footer_font = _font(31, True)
    max_text_width = width - 2 * margin

    brand_text = _normalise_display_text(brand.strip())
    brand_font = _font(31, True)
    brand_width = min(
        draw.textbbox((0, 0), brand_text, font=brand_font)[2] + 42,
        max_text_width,
    )
    draw.rounded_rectangle(
        (margin, 76, margin + brand_width, 136),
        radius=18,
        fill=(210, 38, 48, 245),
    )
    draw.text((margin + 20, 87), brand_text, font=brand_font, fill="white")

    headline = _normalise_display_text(headline.strip())
    headline_lines = _wrap_by_pixels(draw, headline, headline_font, max_text_width)
    while len(headline_lines) > 4 and headline_font.size > 54:
        headline_font = _font(headline_font.size - 4, True)
        headline_lines = _wrap_by_pixels(draw, headline, headline_font, max_text_width)
    if len(headline_lines) > 4:
        raise ValueError("News image headline is too long to render clearly.")

    body = _normalise_display_text(body.strip())
    body_font_size = body_font.size
    body_lines = _wrap_by_pixels(draw, body, body_font, max_text_width - 24)
    while len(body_lines) > 7 and body_font_size > 30:
        body_font_size -= 2
        body_font = _font(body_font_size, False)
        body_lines = _wrap_by_pixels(draw, body, body_font, max_text_width - 24)
    if len(body_lines) > 7:
        body_lines = body_lines[:7]
        body_lines[-1] = body_lines[-1].rstrip(" .,;:") + "…"

    headline_line_height = int(headline_font.size * 1.12)
    body_line_height = int(body_font.size * 1.38)
    content_height = len(headline_lines) * headline_line_height + 34
    content_height += len(body_lines) * body_line_height + 48
    content_top = max(int(height * 0.34), int(height * 0.53 - content_height / 2))
    panel_bottom = content_top + content_height + 52
    if panel_bottom > height - 250:
        content_top = height - 250 - content_height - 52
        panel_bottom = height - 250

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        (32, content_top - 28, width - 32, panel_bottom),
        radius=30,
        fill=(5, 10, 20, 190),
    )
    image = Image.alpha_composite(image, panel)
    draw = ImageDraw.Draw(image)

    y = content_top
    for line in headline_lines:
        draw.text(
            (margin, y),
            line,
            font=headline_font,
            fill="white",
            stroke_width=1,
            stroke_fill=(0, 0, 0, 120),
        )
        y += headline_line_height
    y += 34
    for line in body_lines:
        draw.text((margin, y), line, font=body_font, fill=(238, 242, 247, 255))
        y += body_line_height

    footer_y = height - 210
    footer_text = "LIKE, SHARE AND FOLLOW"
    footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    footer_x = (width - (footer_bbox[2] - footer_bbox[0])) // 2
    draw.text((footer_x, footer_y), footer_text, font=footer_font, fill=(255, 226, 170, 255))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)
    return output_path
