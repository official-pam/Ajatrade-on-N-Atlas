"""Generate pre-computed onboarding voice note mp3 files.

Runnable as ``python -m voice.generate_assets``.

Loops over every script id and language in ``voice.scripts.SCRIPTS``,
calls Spitch TTS for each, and saves the result as an mp3 in
``voice/audio/``. File names are ``{script_id}__{language}__{hash8}.mp3``
so changing a script's text changes the file name and triggers regeneration.

Manifest: ``voice/audio/manifest.json`` maps script_id + language to the
current file name and text hash.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

# Load environment variables (including SPITCH_API_KEY) before importing
# the TTS module, which creates the Spitch client at import time.
from dotenv import load_dotenv

load_dotenv()

from app.services.text_to_speech import text_to_speech

logger = logging.getLogger("generate_assets")

SCRIPTS_MODULE = Path(__file__).resolve().parent
AUDIO_DIR = SCRIPTS_MODULE / "audio"
MANIFEST_PATH = AUDIO_DIR / "manifest.json"

# Languages that have script text in SCRIPTS. Must match the keys used in
# scripts.py so the same language name is passed to text_to_speech().
LANGUAGES = ["english", "hausa", "igbo", "yoruba", "pidgin"]

# WhatsApp audio message limit is 16 MB for upload and 15 minutes for length.
# Onboarding scripts are short, so a sensible upper bound is well under that.
# Welcome_consent scripts run long in some languages (up to ~38 s), so keep
# the ceiling comfortably above that.
MAX_DURATION_SECONDS = 60.0
MIN_DURATION_SECONDS = 2.0

# Path to ffprobe, if available on the system. When present we use it for
# reliable duration detection instead of the built-in heuristic parser.
FFPROBE_PATH = None
try:
    import shutil
    FFPROBE_PATH = shutil.which("ffprobe")
except Exception:
    pass


def sha256_hex8(text: str) -> str:
    """First 8 hex characters of the SHA-256 of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def filename_for(script_id: str, language: str, text: str) -> str:
    """Build the file name for a script + language + text combination."""
    return f"{script_id}__{language}__{sha256_hex8(text)}.mp3"


def load_manifest() -> dict:
    """Return the current manifest, or an empty dict if it does not exist."""
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt or unreadable manifest; starting fresh: %s", exc)
        return {}


def save_manifest(manifest: dict) -> None:
    """Write *manifest* to disk, creating the parent directory if needed."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def get_text_for_script(script_id: str, language: str) -> str | None:
    """Return the script text for *script_id* and *language*.

    Falls back to English when the language is missing, matching the
    behaviour of ``get__welcoming_script``. Returns ``None`` when the
    script id itself does not exist.
    """
    from app.voice.scripts import SCRIPTS

    entry = SCRIPTS.get(script_id)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get(language, entry.get("english", ""))
    # Single-string script (e.g. CONSENT_ACCEPTED_ACK).
    return entry


def get_script_ids() -> list[str]:
    """Return all script ids that should be generated."""
    from app.voice.scripts import SCRIPTS

    return list(SCRIPTS.keys())


async def generate_one(script_id: str, language: str, text: str, force: bool) -> dict:
    """Generate (or skip) one audio file. Returns a result dict."""
    fname = filename_for(script_id, language, text)
    fpath = AUDIO_DIR / fname

    if fpath.exists() and not force:
        logger.info("skipped: %s (%s)", fname, language)
        return {"script_id": script_id, "language": language, "status": "skipped", "file": fname}

    logger.info("generating: %s (%s)", fname, language)
    audio_bytes = await text_to_speech(text, language)
    if audio_bytes is None:
        return {"script_id": script_id, "language": language, "status": "failed", "file": fname, "reason": "TTS returned no audio"}

    if not audio_bytes:
        return {"script_id": script_id, "language": language, "status": "failed", "file": fname, "reason": "TTS returned empty audio"}

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(audio_bytes)

    if not validate_audio_file(fpath):
        return {"script_id": script_id, "language": language, "status": "failed", "file": fname, "reason": "audio file failed validation"}

    return {"script_id": script_id, "language": language, "status": "generated", "file": fname}


def validate_audio_file(fpath: Path) -> bool:
    """Check that an audio file exists, is non-empty, and has a plausible duration."""
    if not fpath.exists():
        logger.error("Validation failed for %s: file does not exist", fpath.name)
        return False
    size = fpath.stat().st_size
    if size == 0:
        logger.error("Validation failed for %s: empty file", fpath.name)
        return False
    # mp3 frame size is typically 144 bytes per 1152 samples at 44.1kHz mono,
    # but a rough heuristic: a valid mp3 with a 40-byte header + some frames.
    # For a more reliable duration check, parse the mp3 headers.
    duration = estimate_mp3_duration_seconds(fpath)
    if duration is None:
        logger.error("Validation failed for %s: could not parse duration", fpath.name)
        return False
    if duration < MIN_DURATION_SECONDS:
        logger.error("Validation failed for %s: too short (%.1f s)", fpath.name, duration)
        return False
    if duration > MAX_DURATION_SECONDS:
        logger.error("Validation failed for %s: too long (%.1f s)", fpath.name, duration)
        return False
    logger.info("Validated %s: %d bytes, %.1f s", fpath.name, size, duration)
    return True


def estimate_mp3_duration_seconds(fpath: Path) -> float | None:
    """Return the duration of an mp3 file in seconds.

    Uses ffprobe when available (reliable), otherwise falls back to a
    heuristic parser for constant-bitrate mp3s.
    """
    if FFPROBE_PATH:
        try:
            result = subprocess.run(
                [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(fpath)],
                capture_output=True, text=True, timeout=10.0,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            pass

    # Fallback heuristic for constant-bitrate mp3s.
    try:
        data = fpath.read_bytes()
    except OSError:
        return None
    if len(data) < 4:
        return None

    pos = 0
    first_frame = False
    while pos < len(data) - 4:
        sync = (data[pos] & 0xFF) | ((data[pos + 1] & 0xFF) << 8)
        if (sync & 0xFFE0) == 0xFFE0:
            if not first_frame:
                first_frame = True
                header = data[pos:pos + 4]
                bitrate_index = (header[2] >> 4) & 0x0F
                sr_index = (header[2] >> 2) & 0x03

                bitrate_table = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
                sample_rate_table = [(44100, 48000, 32000), (22050, 24000, 16000)]

                if bitrate_index >= len(bitrate_table) or bitrate_table[bitrate_index] == 0:
                    return None
                if sr_index >= len(sample_rate_table):
                    return None

                bitrate_kbps = bitrate_table[bitrate_index]
                bytes_per_second = (bitrate_kbps * 1000) / 8.0
                if bytes_per_second > 0:
                    return len(data) / bytes_per_second
        pos += 1

    return None


async def generate_all(force: bool = False, only_script: str | None = None) -> list[dict]:
    """Generate audio for all scripts and languages.

    Returns a list of result dicts, one per (script_id, language) pair.
    """
    results: list[dict] = []
    script_ids = get_script_ids()
    if only_script is not None:
        if only_script not in script_ids:
            logger.error("Unknown script id: %s (known: %s)", only_script, script_ids)
            sys.exit(1)
        script_ids = [only_script]

    for script_id in script_ids:
        for language in LANGUAGES:
            text = get_text_for_script(script_id, language)
            if text is None or not text.strip():
                logger.warning("No text for %s / %s; skipping", script_id, language)
                results.append({"script_id": script_id, "language": language, "status": "skipped", "file": None, "reason": "no text"})
                continue
            result = await generate_one(script_id, language, text, force)
            results.append(result)

    return results


def write_manifest_from_results(results: list[dict]) -> dict:
    """Merge generation results into the existing manifest and save.

    Only successful (generated) entries are added. Existing entries are
    preserved so a partial run (e.g. ``--only``) never destroys a full
    manifest.
    """
    manifest = load_manifest()
    for r in results:
        if r["status"] != "generated":
            continue
        script_id = r["script_id"]
        language = r["language"]
        file_name = r["file"]
        if file_name is None:
            continue
        text = get_text_for_script(script_id, language) or ""
        manifest.setdefault(script_id, {})[language] = {
            "file": file_name,
            "hash": sha256_hex8(text),
        }
    save_manifest(manifest)
    return manifest


def list_orphan_files(manifest: dict) -> list[Path]:
    """Return mp3 files in AUDIO_DIR that are not referenced in *manifest*."""
    if not AUDIO_DIR.exists():
        return []
    referenced: set[str] = set()
    for script_entry in manifest.values():
        for lang_entry in script_entry.values():
            referenced.add(lang_entry.get("file", ""))
    orphans: list[Path] = []
    for p in AUDIO_DIR.iterdir():
        if p.is_file() and p.suffix == ".mp3" and p.name not in referenced:
            orphans.append(p)
    return orphans


def print_summary(results: list[dict]) -> int:
    """Print a summary and return the number of failed entries."""
    generated = sum(1 for r in results if r["status"] == "generated")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = [r for r in results if r["status"] == "failed"]

    print("\n--- Summary ---")
    print(f"generated: {generated}")
    print(f"skipped:   {skipped}")
    print(f"failed:    {len(failed)}")
    for r in failed:
        print(f"  FAILED: {r['script_id']} / {r['language']} — {r.get('reason', 'unknown')}")

    if failed:
        print("\nERROR: one or more files failed to generate.")
    return len(failed)


def check_missing_on_startup(manifest: dict) -> list[str]:
    """Return a list of (script_id, language) pairs missing from the manifest or disk."""
    missing: list[str] = []
    script_ids = get_script_ids()
    for script_id in script_ids:
        manifest_entry = manifest.get(script_id, {})
        for language in LANGUAGES:
            entry = manifest_entry.get(language)
            if entry is None:
                missing.append(f"{script_id} / {language} — not in manifest")
                continue
            fpath = AUDIO_DIR / entry["file"]
            if not fpath.exists():
                missing.append(f"{script_id} / {language} — file missing: {entry['file']}")
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate onboarding voice note mp3 files")
    parser.add_argument("--force", action="store_true", help="Regenerate all files even if they exist")
    parser.add_argument("--only", type=str, default=None, help="Generate only one script id")
    parser.add_argument("--clean", action="store_true", help="List orphan mp3 files not in the manifest (does not delete)")
    parser.add_argument("--check", action="store_true", help="Run startup check for missing files")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    if args.check:
        manifest = load_manifest()
        missing = check_missing_on_startup(manifest)
        if missing:
            print("\n--- Startup check: missing audio ---")
            for m in missing:
                print(f"  WARNING: {m}")
        else:
            print("\n--- Startup check: all audio files present ---")
        return

    if args.clean:
        manifest = load_manifest()
        orphans = list_orphan_files(manifest)
        if orphans:
            print("\n--- Orphan mp3 files (not in manifest) ---")
            for p in orphans:
                print(f"  {p.name}  ({p.stat().st_size} bytes)")
            print(f"\nTotal orphans: {len(orphans)}")
        else:
            print("\nNo orphan mp3 files found.")
        return

    results = asyncio.run(generate_all(force=args.force, only_script=args.only))
    write_manifest_from_results(results)
    failed_count = print_summary(results)

    if failed_count:
        sys.exit(1)

    # Print total size after successful run.
    total_size = sum(p.stat().st_size for p in AUDIO_DIR.glob("*.mp3"))
    file_count = len(list(AUDIO_DIR.glob("*.mp3")))
    print(f"\nTotal audio files: {file_count}")
    print(f"Total size: {total_size} bytes ({total_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
