import json
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
    "disaster": "Major Sad Event",
    "emergencyreporting": "Major Sad Event",
    "trends": "Social trends",
    "achievement": "Other",
    "breakingnews": "Other",
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
