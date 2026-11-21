"""Analyze a downloaded image to find the best region for text overlay."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)


def _region_stats(img: Image.Image, top: int, bottom: int) -> tuple[float, float]:
    """Return (mean_brightness, busyness) for a horizontal strip of *img*.

    *img* must already be in ``"L"`` (grayscale) mode.
    Busyness is approximated as the variance of pixel values — high variance
    means lots of edges / detail, low variance means a smooth area suitable
    for text.
    """
    region = img.crop((0, top, img.width, bottom))
    pixels = (
        list(region.get_flattened_data())
        if hasattr(region, "get_flattened_data")
        else list(region.getdata())
    )
    n = len(pixels)
    if n == 0:
        return 0.0, 0.0

    mean = sum(pixels) / n
    variance = sum((p - mean) ** 2 for p in pixels) / n
    return mean, variance


def analyze_image_layout(image_path: Path) -> dict:
    """Analyze an image and return layout suggestions.

    The image is split into a **top third** and a **bottom third**.  For each
    region we compute:

    * **brightness** — average grayscale pixel value (0-255).
    * **busyness** — pixel-value variance; higher means more detail / edges.

    The region with *lower* busyness is recommended for text overlay because
    text is easier to read over a smooth area.  When both regions are
    similarly busy (within 10 % of each other) we default to ``"bottom"``
    following the common news-graphic convention.

    Returns
    -------
    dict
        ``suggested_text_position`` (``"top"`` or ``"bottom"``),
        ``top_brightness``, ``bottom_brightness``,
        ``top_busyness``, ``bottom_busyness``.
    """
    log.info("Analyzing image layout: %s", image_path)

    with Image.open(image_path) as img:
        gray = img.convert("L")

    third = gray.height // 3

    top_brightness, top_busyness = _region_stats(gray, 0, third)
    bottom_brightness, bottom_busyness = _region_stats(gray, gray.height - third, gray.height)

    # Decide where to place text
    # If the two regions are within 10% of each other, default to bottom
    max_busyness = max(top_busyness, bottom_busyness, 1.0)
    relative_diff = abs(top_busyness - bottom_busyness) / max_busyness

    if relative_diff < 0.10:
        suggested = "bottom"
    elif top_busyness < bottom_busyness:
        suggested = "top"
    else:
        suggested = "bottom"

    result = {
        "suggested_text_position": suggested,
        "top_brightness": round(top_brightness, 2),
        "bottom_brightness": round(bottom_brightness, 2),
        "top_busyness": round(top_busyness, 2),
        "bottom_busyness": round(bottom_busyness, 2),
    }

    log.info(
        "Layout analysis — position=%s  top(bright=%.1f, busy=%.1f)  bottom(bright=%.1f, busy=%.1f)",
        result["suggested_text_position"],
        top_brightness,
        top_busyness,
        bottom_brightness,
        bottom_busyness,
    )
    return result
