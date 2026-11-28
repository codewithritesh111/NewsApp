import json
import subprocess
from collections.abc import Sequence
from pathlib import Path


REEL_DURATION_SECONDS = 15
ENDCLIP_DURATION_SECONDS = 3.0
REEL_FRAME_RATE = 30
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
BGM_DIRECTORY = Path(__file__).resolve().parents[2] / "BGM"
BGM_ROTATION_PATH = Path(__file__).resolve().parents[2] / "bgm_rotation.json"
ENDCLIP_DIRECTORY = Path(__file__).resolve().parents[2] / "EndClip"
ENDCLIP_BGM_PATH = BGM_DIRECTORY / "EndClip" / "News Outro Sting.mp3"
REEL_ROTATION_PATH = Path(__file__).resolve().parents[2] / "reel_rotation.json"
SUPPORTED_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


REEL_TRANSITIONS = (
    "smoothleft",
    "smoothright",
    "slideleft",
    "slideright",
    "coverleft",
    "coverup",
    "wipeleft",
    "wiperight",
    "fade",
    "dissolve",
    "zoomin",
    "hblur",
)


CATEGORY_BGM_ALIASES = {
    "sportsandcricket": "Achievement",
    "technologyandartificialintelligence": "Business",
    "entertainment": "Trends",
    "scienceandspace": "Achievement",
    "businessandstartups": "Business",
    "educationandcareers": "Other",
    "amazingandunusualnews": "Trends",
    "usefulpublicinformation": "BreakingNews",
    # Legacy category mappings
    "technology": "Business",
    "defence": "Geopolitics",
    "governmentschemes": "Politics",
    "socialtrends": "Trends",
    "majorsadevent": "Disaster",
    "majorgoodevent": "Achievement",
    "sciencetech": "Business",
    "naturaldisaster": "Disaster",
    "majorachievement": "Achievement",
    "celebritysports": "Trends",
    "unusual": "Trends",
}


def _category_key(value: str) -> str:
    return value.casefold().replace(" ", "")


def _read_rotation_state(state_path: Path) -> dict[str, int]:
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


def _write_rotation_state(state_path: Path, state: dict[str, int]) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = state_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        temp_file.replace(state_path)
    except Exception:
        pass


def _next_round_robin_item(items: Sequence, key: str, state_path: Path | None):
    if len(items) == 1 or state_path is None:
        return items[0]
    state = _read_rotation_state(state_path)
    idx = state.get(key, 0)
    chosen = items[idx % len(items)]
    state[key] = (idx + 1) % len(items)
    _write_rotation_state(state_path, state)
    return chosen


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

    if not candidates:
        aliased = CATEGORY_BGM_ALIASES.get(cat_key)
        if aliased:
            candidates = get_category_bgm_tracks(aliased, bgm_dir)

    if not candidates:
        candidates = get_category_bgm_tracks("other", bgm_dir)

    if not candidates:
        raise FileNotFoundError(
            f"No BGM track matches category {category!r}, and no Other fallback track "
            f"was found in {bgm_dir}."
        )

    effective_state_path = rotation_state_path
    if effective_state_path is None:
        try:
            if bgm_dir.resolve() == BGM_DIRECTORY.resolve():
                effective_state_path = BGM_ROTATION_PATH
        except Exception:
            pass

    return _next_round_robin_item(candidates, cat_key, effective_state_path)


def select_reel_transition(rotation_state_path: Path | None = None) -> str:
    effective_state = rotation_state_path if rotation_state_path is not None else REEL_ROTATION_PATH
    return _next_round_robin_item(REEL_TRANSITIONS, "transition", effective_state)


def select_endclip_video(
    endclip_dir: Path = ENDCLIP_DIRECTORY,
    rotation_state_path: Path | None = None,
) -> Path | None:
    if not endclip_dir.is_dir():
        return None
    clips = sorted(
        (p for p in endclip_dir.iterdir() if p.is_file() and p.suffix.casefold() == ".mp4"),
        key=lambda p: p.name.casefold(),
    )
    if not clips:
        return None
    effective_state = rotation_state_path
    if effective_state is None:
        try:
            if endclip_dir.resolve() == ENDCLIP_DIRECTORY.resolve():
                effective_state = REEL_ROTATION_PATH
        except Exception:
            pass
    return _next_round_robin_item(clips, "endclip", effective_state)


def _compute_reel_timing(num_slides: int, has_endclip: bool) -> tuple[float, float, float, float]:
    """Return (slide_dur, trans_dur, end_dur, total_dur) strictly within 15-30s."""
    end_dur = ENDCLIP_DURATION_SECONDS if has_endclip else 0.0
    if num_slides <= 1:
        if has_endclip:
            trans_dur = 0.5
            slide_dur = 12.5
            total_dur = 15.0
        else:
            trans_dur = 0.0
            slide_dur = float(REEL_DURATION_SECONDS)
            total_dur = float(REEL_DURATION_SECONDS)
        return slide_dur, trans_dur, end_dur, total_dur

    trans_dur = 0.75
    if num_slides == 2:
        slide_dur = 9.75 if has_endclip else 10.375
    elif num_slides == 3:
        slide_dur = 8.5 if has_endclip else 9.0
    else:
        target_total = 28.0
        transitions_count = num_slides - 1 + (1 if has_endclip else 0)
        slide_dur = max(4.0, (target_total - end_dur + transitions_count * trans_dur) / num_slides)

    transitions_count = num_slides - 1 + (1 if has_endclip else 0)
    total_dur = num_slides * slide_dur + end_dur - transitions_count * trans_dur
    return slide_dur, trans_dur, end_dur, total_dur


def _ffmpeg_color(accent_hex: str | None) -> str:
    if isinstance(accent_hex, str):
        cleaned = accent_hex.strip().lstrip("#")
        if len(cleaned) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in cleaned):
            return f"0x{cleaned.upper()}"
    return "0xEF4444"


def _motion_expr(slide_idx: int, total_frames: int) -> tuple[str, str, str]:
    mode = slide_idx % 3
    if mode == 0:
        return (
            f"1+0.07*(on/{total_frames})",
            "iw/2-(iw/zoom/2)",
            "ih/2-(ih/zoom/2)",
        )
    if mode == 1:
        return (
            f"1.07-0.07*(on/{total_frames})",
            "iw/2-(iw/zoom/2)",
            "ih/2-(ih/zoom/2)",
        )
    return (
        f"1+0.06*(on/{total_frames})",
        "iw/2-(iw/zoom/2)",
        f"(ih-ih/zoom)*(0.5-0.12*(on/{total_frames}))",
    )


def _normalize_slide_specs(
    image_path: Path,
    background_path: Path | None,
    overlay_path: Path | None,
    slides: Sequence[dict[str, Path]] | None,
) -> list[tuple[Path, Path | None, Path | None]]:
    if slides:
        normalized: list[tuple[Path, Path | None, Path | None]] = []
        for entry in slides:
            img = Path(entry["image_path"])
            if not img.is_file():
                raise FileNotFoundError(f"Reel image not found: {img}")
            bg = entry.get("background_path")
            ov = entry.get("overlay_path")
            resolved_bg = Path(bg) if bg and Path(bg).is_file() else None
            resolved_ov = Path(ov) if ov and Path(ov).is_file() else None
            normalized.append((img, resolved_bg, resolved_ov))
        return normalized

    if not image_path.is_file():
        raise FileNotFoundError(f"Reel image not found: {image_path}")
    resolved_bg = background_path or image_path.with_name("reel_background.jpg")
    resolved_ov = overlay_path or image_path.with_name("reel_overlay.png")
    return [
        (
            image_path,
            resolved_bg if resolved_bg.is_file() else None,
            resolved_ov if resolved_ov.is_file() else None,
        )
    ]


def create_static_reel(
    image_path: Path,
    output_path: Path,
    audio_path: Path,
    *,
    background_path: Path | None = None,
    overlay_path: Path | None = None,
    slides: Sequence[dict[str, Path]] | None = None,
    transition: str | None = None,
    endclip_path: Path | None = None,
    endclip_bgm_path: Path | None = None,
    accent_hex: str | None = None,
) -> Path:
    if not audio_path.is_file():
        raise FileNotFoundError(f"Reel background music not found: {audio_path}")

    slide_specs = _normalize_slide_specs(image_path, background_path, overlay_path, slides)
    resolved_endclip = endclip_path if endclip_path and endclip_path.is_file() else None
    resolved_endclip_bgm = (
        endclip_bgm_path if endclip_bgm_path and endclip_bgm_path.is_file() else None
    )
    chosen_transition = transition if transition in REEL_TRANSITIONS else REEL_TRANSITIONS[0]
    timeline_color = _ffmpeg_color(accent_hex)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "Static Reel encoding requires imageio-ffmpeg. Install project dependencies "
            "with `pip install -r requirements.txt`."
        ) from exc

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    num_slides = len(slide_specs)
    slide_dur, trans_dur, end_dur, total_dur = _compute_reel_timing(
        num_slides, resolved_endclip is not None
    )
    slide_frames = int(round(slide_dur * REEL_FRAME_RATE))

    input_args: list[str] = []
    filter_parts: list[str] = []
    stream_labels: list[str] = []
    next_input_idx = 0

    for idx, (img_p, bg_p, ov_p) in enumerate(slide_specs):
        if bg_p is not None and ov_p is not None:
            bg_idx = next_input_idx
            ov_idx = next_input_idx + 1
            next_input_idx += 2
            input_args += [
                "-i", str(bg_p.resolve()),
                "-loop", "1", "-t", f"{slide_dur + 1.0:.2f}", "-i", str(ov_p.resolve()),
            ]
            zoom_expr, x_expr, y_expr = _motion_expr(idx, slide_frames)
            filter_parts.append(
                f"[{bg_idx}:v]scale={REEL_WIDTH * 2}:{REEL_HEIGHT * 2}:flags=lanczos,"
                f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
                f"d={slide_frames}:s={REEL_WIDTH}x{REEL_HEIGHT}:fps={REEL_FRAME_RATE}[z{idx}];"
                f"[z{idx}][{ov_idx}:v]overlay=0:0:shortest=1,"
                f"settb=1/{REEL_FRAME_RATE},setsar=1,format=yuv420p[s{idx}]"
            )
        else:
            still_idx = next_input_idx
            next_input_idx += 1
            input_args += [
                "-loop", "1", "-framerate", str(REEL_FRAME_RATE),
                "-t", f"{slide_dur + 1.0:.2f}", "-i", str(img_p.resolve()),
            ]
            filter_parts.append(
                f"[{still_idx}:v]scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={REEL_WIDTH}:{REEL_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
                f"trim=duration={slide_dur:.3f},setpts=PTS-STARTPTS,"
                f"fps={REEL_FRAME_RATE},settb=1/{REEL_FRAME_RATE},setsar=1,format=yuv420p[s{idx}]"
            )
        stream_labels.append(f"s{idx}")

    if resolved_endclip is not None:
        ec_idx = next_input_idx
        next_input_idx += 1
        input_args += ["-i", str(resolved_endclip.resolve())]
        filter_parts.append(
            f"[{ec_idx}:v]trim=duration={end_dur:.3f},setpts=PTS-STARTPTS,"
            f"scale={REEL_WIDTH}:{REEL_HEIGHT}:flags=lanczos,"
            f"fps={REEL_FRAME_RATE},settb=1/{REEL_FRAME_RATE},setsar=1,format=yuv420p[ec]"
        )
        stream_labels.append("ec")

    # Apply xfade transitions across slides and into the EndClip
    if len(stream_labels) == 1:
        combined_label = stream_labels[0]
    else:
        prev_label = stream_labels[0]
        for step_idx in range(1, len(stream_labels)):
            next_label = stream_labels[step_idx]
            offset = step_idx * (slide_dur - trans_dur)
            out_label = f"xf{step_idx}"
            filter_parts.append(
                f"[{prev_label}][{next_label}]xfade=transition={chosen_transition}:"
                f"duration={trans_dur:.3f}:offset={offset:.3f}[{out_label}]"
            )
            prev_label = out_label
        combined_label = prev_label

    # Small bottom timeline covering the entire reel including the EndClip
    filter_parts.append(
        f"[{combined_label}]"
        f"drawbox=x=48:y=1896:w=984:h=8:color=0x000000@0.45:t=fill,"
        f"drawbox=x=50:y=1898:w=980:h=4:color=0xFFFFFF@0.28:t=fill,"
        f"drawbox=x=50:y=1898:w='min(980,max(4,980*(t/{total_dur:.3f})))':h=4:"
        f"color={timeline_color}@0.95:t=fill,format=yuv420p[vout]"
    )

    # Audio pipeline: main news BGM + optional distinct EndClip outro BGM
    main_bgm_idx = next_input_idx
    next_input_idx += 1
    input_args += ["-stream_loop", "-1", "-i", str(audio_path.resolve())]

    if resolved_endclip is not None and resolved_endclip_bgm is not None:
        end_bgm_idx = next_input_idx
        next_input_idx += 1
        input_args += ["-stream_loop", "-1", "-i", str(resolved_endclip_bgm.resolve())]
        endclip_start = max(0.0, total_dur - end_dur)
        main_audio_end = min(total_dur, endclip_start + 0.45)
        delay_ms = int(round(endclip_start * 1000))
        filter_parts.append(
            f"[{main_bgm_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"atrim=duration={main_audio_end:.3f},asetpts=PTS-STARTPTS,"
            f"loudnorm=I=-14:TP=-1.5:LRA=9,"
            f"afade=t=in:st=0:d=0.35,"
            f"afade=t=out:st={max(0.1, main_audio_end - 0.6):.3f}:d=0.6[amain];"
            f"[{end_bgm_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"atrim=duration={end_dur:.3f},asetpts=PTS-STARTPTS,"
            f"loudnorm=I=-14:TP=-1.5:LRA=9,"
            f"afade=t=in:st=0:d=0.25,"
            f"afade=t=out:st={max(0.1, end_dur - 0.55):.3f}:d=0.55,"
            f"adelay={delay_ms}|{delay_ms}[aend];"
            f"[amain][aend]amix=inputs=2:duration=longest:normalize=0,atrim=duration={total_dur:.3f}[aout]"
        )
    else:
        filter_parts.append(
            f"[{main_bgm_idx}:a]atrim=duration={total_dur:.3f},asetpts=PTS-STARTPTS,"
            f"loudnorm=I=-14:TP=-1.5:LRA=9,"
            f"afade=t=in:st=0:d=0.35,"
            f"afade=t=out:st={max(0.1, total_dur - 1.2):.3f}:d=1.2[aout]"
        )

    total_frames = int(round(total_dur * REEL_FRAME_RATE))
    command = [
        ffmpeg_exe,
        "-y",
        *input_args,
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-frames:v",
        str(total_frames),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
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
        f"{total_dur:.3f}",
        "-movflags",
        "+faststart",
        str(output_path.resolve()),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg Reel encoding failed:\n{result.stderr}")
    return output_path
