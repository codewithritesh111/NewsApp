import subprocess
from pathlib import Path


REEL_DURATION_SECONDS = 15
REEL_FRAME_RATE = 30
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
BGM_DIRECTORY = Path(__file__).resolve().parents[2] / "BGM"
SUPPORTED_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


def _category_key(value: str) -> str:
    return value.casefold().replace(" ", "")


def select_bgm_track(category: str, bgm_dir: Path = BGM_DIRECTORY) -> Path:
    if not bgm_dir.is_dir():
        raise FileNotFoundError(f"Background music directory not found: {bgm_dir}")

    tracks = sorted(
        (
            track
            for track in bgm_dir.iterdir()
            if track.is_file() and track.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
        ),
        key=lambda track: (track.suffix.casefold(), track.name.casefold()),
    )
    matching_key = _category_key(category)
    for track in tracks:
        if _category_key(track.stem) == matching_key:
            return track

    for track in tracks:
        if _category_key(track.stem) == "other":
            return track
    raise FileNotFoundError(
        f"No BGM track matches category {category!r}, and no Other fallback track "
        f"was found in {bgm_dir}."
    )


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
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not encode the 15-second Reel with BGM: {exc}") from exc
    if result.returncode:
        details = (result.stderr or "").strip().splitlines()
        error = details[-1] if details else f"ffmpeg exited with code {result.returncode}"
        raise RuntimeError(f"Could not encode the 15-second Reel with BGM: {error}")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("The Reel encoder did not produce a non-empty MP4.")
    return output_path
