import concurrent.futures
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
import imageio_ffmpeg

TRACKS = {
    "BreakingNews": [
        "News Theme.mp3",
        "Volatile Reaction.mp3",
        "Stormfront.mp3",
    ],
    "Politics": [
        "The Complex.mp3",
        "Division.mp3",
        "Symmetry.mp3",
    ],
    "Geopolitics": [
        "Impending Boom.mp3",
        "Final Count.mp3",
        "Clash Defiant.mp3",
    ],
    "Business": [
        "Cipher2.mp3",
        "Motivator.mp3",
        "Protofunk.mp3",
    ],
    "Crime": [
        "Private Eye.mp3",
        "Covert Affair.mp3",
        "I Can Feel it Coming.mp3",
    ],
    "Disaster": [
        "Heartbreaking.mp3",
        "Anguish.mp3",
        "Sad Trio.mp3",
    ],
    "Achievement": [
        "Strength of the Titans.mp3",
        "Impact Allegretto.mp3",
        "Hero Down.mp3",
    ],
    "Trends": [
        "Carefree.mp3",
        "Sneaky Adventure.mp3",
        "Airport Lounge.mp3",
    ],
    "EmergencyReporting": [
        "Hitman.mp3",
        "Corruption.mp3",
        "Danger Storm.mp3",
    ],
}

ROOT_DIR = Path(__file__).resolve().parents[1]
BGM_DIR = ROOT_DIR / "BGM"
TEMP_DIR = ROOT_DIR / "tmp_bgm_downloads"
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


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
            "Range": "bytes=0-1200000",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        raw_temp.write_bytes(data)

    cmd = [
        FFMPEG_EXE,
        "-y",
        "-ss", "0",
        "-t", "15",
        "-i", str(raw_temp),
        "-af", "afade=t=out:st=13.5:d=1.5",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(dest_file),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if raw_temp.is_file():
        raw_temp.unlink()

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed for {category}/{filename}: {result.stderr}")

    return f"[SAVED] {category}/{filename} ({dest_file.stat().st_size} bytes)"


def download_and_trim_all() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    tasks = []

    for category, filenames in TRACKS.items():
        for filename in filenames:
            tasks.append((category, filename))

    print(f"Starting parallel download and processing for {len(tasks)} tracks...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_track = {
            executor.submit(process_single_track, cat, fn): (cat, fn)
            for cat, fn in tasks
        }
        for future in concurrent.futures.as_completed(future_to_track):
            cat, fn = future_to_track[future]
            try:
                msg = future.result()
                print(msg, flush=True)
            except Exception as exc:
                print(f"[ERROR] {cat}/{fn}: {exc}", flush=True)

    if TEMP_DIR.is_dir():
        import shutil
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    # Clean up redundant legacy copies from root BGM directory to save git repo space
    for old_file in ("Business.mp3", "Crime.mp3", "Geopolitics.mp3", "Politics.mp3"):
        old_path = BGM_DIR / old_file
        if old_path.is_file():
            old_path.unlink()
            print(f"[REMOVED] Legacy redundant file: {old_file}", flush=True)

    print("\nAll BGM tracks processed and organized successfully!", flush=True)


if __name__ == "__main__":
    download_and_trim_all()
