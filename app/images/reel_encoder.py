import json
import re
import subprocess
from pathlib import Path


REEL_DURATION_SECONDS = 15
REEL_FRAME_RATE = 30
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
BGM_DIRECTORY = Path(__file__).resolve().parents[2] / "BGM"
BGM_ROTATION_PATH = Path(__file__).resolve().parents[2] / "bgm_rotation.json"
SUPPORTED_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


CATEGORY_BGM_ALIASES = {
    "shockingcrime": "Crime",
    "naturaldisaster": "Disaster",
    "majorachievement": "Achievement",
    "celebritysports": "Trends",
    "sciencetech": "Business",
    "unusual": "Trends",
    "disaster": "Disaster",
    "emergencyreporting": "EmergencyReporting",
    "trends": "Trends",
    "achievement": "Achievement",
    "breakingnews": "BreakingNews",
    "technology": "Other",
    "defence": "BreakingNews",
    "majorgoodevent": "Achievement",
    "majorsadevent": "Disaster",
    "governmentschemes": "Government schemes",
    "socialtrends": "Trends",
}


def _category_key(value: str) -> str:
    return value.casefold().replace(" ", "")


def _read_bgm_rotation_state(state_path: Path) -> dict[str, int]:
    if not state_path.is_file():
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {
                    str(k).lower(): int(v)
                    for k, v in data.items()
                    if isinstance(v, (int, float))
                }
    except Exception:
        pass
    return {}


def _write_bgm_rotation_state(state_path: Path, state: dict[str, int]) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = state_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        temp_file.replace(state_path)
    except Exception:
        pass


def get_category_bgm_tracks(category: str, bgm_dir: Path = BGM_DIRECTORY) -> list[Path]:
    """Finds all audio tracks matching a category (directory or files)."""
    if not bgm_dir.is_dir():
        return []

    cat_key = _category_key(category)

    # 1. Look for a category subdirectory (e.g. BGM/BreakingNews)
    for entry in sorted(bgm_dir.iterdir()):
        if entry.is_dir() and _category_key(entry.name) == cat_key:
            tracks = sorted(
                (
                    track
                    for track in entry.iterdir()
                    if track.is_file() and track.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
                ),
                key=lambda track: track.name.casefold(),
            )
            if tracks:
                return tracks

    # 2. Look for matching flat files in bgm_dir (e.g. Crime.mp3 or Crime_1.mp3)
    flat_tracks = sorted(
        (
            track
            for track in bgm_dir.iterdir()
            if track.is_file()
            and track.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
            and _category_key(track.stem).startswith(cat_key)
        ),
        key=lambda track: track.name.casefold(),
    )
    if flat_tracks:
        return flat_tracks

    return []


def select_bgm_track(
    category: str,
    bgm_dir: Path = BGM_DIRECTORY,
    rotation_state_path: Path | None = None,
) -> Path:
    if not bgm_dir.is_dir():
        raise FileNotFoundError(f"Background music directory not found: {bgm_dir}")

    cat_key = _category_key(category)
    candidates = get_category_bgm_tracks(category, bgm_dir)

    # Check aliases if no direct candidates
    if not candidates:
        aliased = CATEGORY_BGM_ALIASES.get(cat_key)
        if aliased:
            candidates = get_category_bgm_tracks(aliased, bgm_dir)

    # Fallback to 'other'
    if not candidates:
        candidates = get_category_bgm_tracks("other", bgm_dir)

    if not candidates:
        raise FileNotFoundError(
            f"No BGM track matches category {category!r}, and no Other fallback track "
            f"was found in {bgm_dir}."
        )

    if len(candidates) == 1:
        return candidates[0]

    # Determine effective state path for round-robin rotation
    effective_state_path = rotation_state_path
    if effective_state_path is None:
        try:
            if bgm_dir.resolve() == BGM_DIRECTORY.resolve():
                effective_state_path = BGM_ROTATION_PATH
        except Exception:
            pass

    if effective_state_path:
        state = _read_bgm_rotation_state(effective_state_path)
        idx = state.get(cat_key, 0)
        selected_track = candidates[idx % len(candidates)]
        state[cat_key] = (idx + 1) % len(candidates)
        _write_bgm_rotation_state(effective_state_path, state)
        return selected_track

    return candidates[0]


def create_static_reel(image_path: Path, output_path: Path, audio_path: Path) -> Path:
    if not image_path.is_file():
        raise FileNotFoundError(f"Reel image not found: {image_path}")
    if not audio_path.is_file():
        raise FileNotFoundError(f"Reel background music not found: {audio_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "Static Reel encoding requires imageio-ffmpeg. Install project dependencies "
            "with `pip install -r requirements.txt`."
        ) from exc

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(REEL_FRAME_RATE),
        "-i",
        str(image_path.resolve()),
        "-stream_loop",
        "-1",
        "-i",
        str(audio_path.resolve()),
        "-frames:v",
        str(REEL_DURATION_SECONDS * REEL_FRAME_RATE),
        "-vf",
        (
            f"scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={REEL_WIDTH}:{REEL_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
            "format=yuv420p"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-r",
        str(REEL_FRAME_RATE),
        "-t",
        str(REEL_DURATION_SECONDS),
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path.resolve()),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg Reel encoding failed:\n{result.stderr}")
    return output_path


def inspect_video_details(video_path: Path | str) -> dict:
    """Inspect a video file and return detailed metadata (duration, audio, dims, aspect ratio)."""
    video_path = Path(video_path)
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return {
            "duration": 20.0,
            "has_audio": False,
            "width": 1080,
            "height": 1920,
            "aspect_ratio": 1920 / 1080,
            "is_9_16": True,
        }

    command = [ffmpeg_exe, "-hide_banner", "-i", str(video_path.resolve())]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    output = result.stderr or ""

    duration = 20.0 if not video_path.is_file() else 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if match:
        h, m, s = match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    has_audio = bool(re.search(r"Stream\s*#\d+:\d+.*Audio:", output))

    width, height = 1080, 1920
    dim_match = re.search(r"Stream\s*#\d+:\d+.*Video:.*?(\d{2,5})x(\d{2,5})", output)
    if dim_match:
        width = int(dim_match.group(1))
        height = int(dim_match.group(2))

    aspect_ratio = height / max(1, width)
    is_9_16 = height >= width or "9:16" in output or aspect_ratio >= 1.0

    return {
        "duration": max(0.0, duration),
        "has_audio": has_audio,
        "width": width,
        "height": height,
        "aspect_ratio": aspect_ratio,
        "is_9_16": is_9_16,
    }


def inspect_video(video_path: Path | str) -> tuple[float, bool]:
    """Inspect a video file and return (duration_in_seconds, has_audio_stream)."""
    details = inspect_video_details(video_path)
    return max(0.1, details["duration"]), details["has_audio"]


def create_video_reel(
    video_path: Path | str,
    overlay_path: Path | str,
    output_path: Path | str,
    bgm_path: Path | str | None = None,
) -> Path:
    """Superimpose text overlay on video without trimming, requiring duration between 20s and 60s."""
    video_path = Path(video_path)
    overlay_path = Path(overlay_path)
    output_path = Path(output_path)
    bgm_path = Path(bgm_path) if bgm_path else None
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not overlay_path.is_file():
        raise FileNotFoundError(f"Overlay image not found: {overlay_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("Video Reel encoding requires imageio-ffmpeg.") from exc

    raw_duration, has_audio = inspect_video(video_path)

    # Strictly require video duration between 20 sec and 1 min (60 sec)
    if raw_duration < 20.0 or raw_duration > 60.0:
        raise ValueError(
            f"Video duration {raw_duration:.1f}s is outside required [20s, 60s] range. "
            "Only news videos with length strictly between 20 seconds and 1 minute can be used."
        )

    # No need to trim video: preserve full duration as-is without looping
    target_duration = round(raw_duration, 2)

    filter_complex = (
        f"[0:v]scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={REEL_WIDTH}:{REEL_HEIGHT}[vbg];"
        f"[vbg][1:v]overlay=0:0:format=auto[vout]"
    )

    if has_audio:
        # Keep original audio, do not add BGM, do not loop or trim video
        command = [
            ffmpeg_exe,
            "-y",
            "-i", str(video_path.resolve()),
            "-i", str(overlay_path.resolve()),
            "-t", str(target_duration),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-r", str(REEL_FRAME_RATE),
            "-movflags", "+faststart",
            str(output_path.resolve()),
        ]
    else:
        # Silent video, add BGM (BGM loops if shorter than video, fades out at video end)
        if bgm_path and bgm_path.is_file():
            fade_start = max(0.0, target_duration - 1.0)
            audio_filter = f"[2:a]afade=t=out:st={fade_start:.2f}:d=1.0[aout]"
            full_filter = f"{filter_complex};{audio_filter}"
            command = [
                ffmpeg_exe,
                "-y",
                "-i", str(video_path.resolve()),
                "-i", str(overlay_path.resolve()),
                "-stream_loop", "-1",
                "-i", str(bgm_path.resolve()),
                "-t", str(target_duration),
                "-filter_complex", full_filter,
                "-map", "[vout]",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-r", str(REEL_FRAME_RATE),
                "-movflags", "+faststart",
                str(output_path.resolve()),
            ]
        else:
            command = [
                ffmpeg_exe,
                "-y",
                "-i", str(video_path.resolve()),
                "-i", str(overlay_path.resolve()),
                "-t", str(target_duration),
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-r", str(REEL_FRAME_RATE),
                "-movflags", "+faststart",
                str(output_path.resolve()),
            ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg Video Reel encoding failed:\n{result.stderr}")
    return output_path
