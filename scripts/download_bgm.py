import concurrent.futures
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
import imageio_ffmpeg

TRACKS = {
    "BreakingNews": [
        "News Theme.mp3",
        "Dramatic News Pulse.mp3",
        "Epical Drums.mp3",
        "Volatile Reaction.mp3",
    ],
    "Politics": [
        "The Complex.mp3",
        "Division.mp3",
        "Symmetry.mp3",
        "Crusade.mp3",
    ],
    "Geopolitics": [
        "Clash Defiant.mp3",
        "Final Count.mp3",
        "Impending Boom.mp3",
        "Rising Tide.mp3",
    ],
    "Business": [
        "Motivator.mp3",
        "Protofunk.mp3",
        "Tech Live.mp3",
        "Inspired.mp3",
    ],
    "Crime": [
        "Dark Suspense Echoes.mp3",
        "Private Eye.mp3",
        "Covert Affair.mp3",
        "I Can Feel it Coming.mp3",
    ],
    "Disaster": [
        "Deep Atmospheric Sadness.mp3",
        "Somber Cinematic.mp3",
        "Heartbreaking.mp3",
        "Anguish.mp3",
    ],
    "Achievement": [
        "Epic Highlights.mp3",
        "Strength of the Titans.mp3",
        "Hero Down.mp3",
        "Unwritten Return.mp3",
    ],
    "Trends": [
        "Viral Trap Beat.mp3",
        "Modern Urban Groove.mp3",
        "Punchy Reel Synth.mp3",
        "Rocket Power.mp3",
    ],
    "EmergencyReporting": [
        "Danger Storm.mp3",
        "Hitman.mp3",
        "Corruption.mp3",
        "Controlled Chaos.mp3",
    ],
    "Other": [
        "District Four.mp3",
        "Take a Chance.mp3",
        "Mighty Like Us.mp3",
        "Electro Cabello.mp3",
    ],
}

ROOT_DIR = Path(__file__).resolve().parents[1]
BGM_DIR = ROOT_DIR / "BGM"
TEMP_DIR = ROOT_DIR / "tmp_bgm_downloads"
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
CLIP_SECONDS = 18


def find_best_start(src: Path, clip_len: int = CLIP_SECONDS) -> int:
    probe = subprocess.run([FFMPEG_EXE, "-i", str(src)], capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr)
    duration = float(int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))) if match else 60.0
    max_start = max(0, int(duration - clip_len - 2))
    candidates = list(range(0, min(max_start + 1, 76), 10)) or [0]
    best_ss, best_vol = 0, -99.0
    for ss in candidates:
        result = subprocess.run(
            [FFMPEG_EXE, "-ss", str(ss), "-t", str(clip_len), "-i", str(src), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True,
            text=True,
        )
        mv = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", result.stderr)
        if mv:
            vol = float(mv.group(1))
            if vol > best_vol + 0.8 or (ss > 0 and best_vol < -22.0 and vol > best_vol):
                best_vol = vol
                best_ss = ss
    return best_ss


def process_single_track(category: str, filename: str) -> str:
    cat_dir = BGM_DIR / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    dest_file = cat_dir / filename

    if dest_file.is_file() and dest_file.stat().st_size > 50000:
        return f"[EXISTS] {category}/{filename} ({dest_file.stat().st_size} bytes)"

    raw_temp = TEMP_DIR / f"{category}_{filename}"
    url = f"https://incompetech.com/music/royalty-free/mp3-royaltyfree/{urllib.parse.quote(filename)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Range": "bytes=0-3500000",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw_temp.write_bytes(resp.read())

    start_sec = find_best_start(raw_temp, CLIP_SECONDS)
    cmd = [
        FFMPEG_EXE,
        "-y",
        "-ss", str(start_sec),
        "-t", str(CLIP_SECONDS),
        "-i", str(raw_temp),
        "-af", f"loudnorm=I=-14:TP=-1.5:LRA=9,afade=t=in:st=0:d=0.35,afade=t=out:st={CLIP_SECONDS - 1.2}:d=1.2",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "44100",
        str(dest_file),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if raw_temp.is_file():
        raw_temp.unlink()

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed for {category}/{filename}: {result.stderr}")

    return f"[SAVED] {category}/{filename} (start={start_sec}s, {dest_file.stat().st_size} bytes)"


def download_and_trim_all() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    tasks = [
        (category, filename)
        for category, filenames in TRACKS.items()
        for filename in filenames
    ]

    print(f"Starting parallel download and processing for {len(tasks)} tracks...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_track = {
            executor.submit(process_single_track, cat, fn): (cat, fn)
            for cat, fn in tasks
        }
        for future in concurrent.futures.as_completed(future_to_track):
            cat, fn = future_to_track[future]
            try:
                print(future.result(), flush=True)
            except Exception as exc:
                print(f"[ERROR] {cat}/{fn}: {exc}", flush=True)

    if TEMP_DIR.is_dir():
        import shutil
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    print("\nAll BGM tracks processed and organized successfully!", flush=True)


if __name__ == "__main__":
    download_and_trim_all()
