import asyncio
import subprocess


def _convert_to_ogg_opus(audio_bytes: bytes) -> bytes | None:
    result = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "32k",
            "-ar", "48000", "-ac", "1",
            "-f", "ogg", "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr.decode(errors='ignore')}")
        return None
    return result.stdout


async def to_ogg_opus(audio_bytes: bytes) -> bytes | None:
    try:
        return await asyncio.to_thread(_convert_to_ogg_opus, audio_bytes)
    except FileNotFoundError:
        print("ffmpeg not found. Install it and make sure it is on your PATH.")
        return None