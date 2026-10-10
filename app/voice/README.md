# Voice Onboarding Audio Assets

Pre-generated onboarding voice notes for the AjaTrade voice pipeline.

## Where files live

- Audio files: `app/voice/audio/*.mp3`
- Manifest: `app/voice/audio/manifest.json`

The manifest maps each `script_id` + `language` to the current file name and
text hash, for example:

```json
{
  "welcome_consent": {
    "yoruba": {
      "file": "welcome_consent__yoruba__a3f9c2d1.mp3",
      "hash": "a3f9c2d1"
    }
  }
}
```

## Scripts

All script source text lives in `app/voice/scripts.py` in the `SCRIPTS`
dictionary. Each script has a text version for every language
(english, hausa, igbo, yoruba, pidgin).

## How to edit a script

1. Edit the text in `app/voice/scripts.py` inside the `SCRIPTS` dictionary.
2. Re-run the generator (see below). Only the changed scripts regenerate,
   because file names include a hash of the script text.

Mark any wording a native speaker should verify with a `# REVIEW` comment in
the scripts file.

## How to regenerate audio

```bash
.venv/Scripts/python -m app.voice.generate_assets --force
```

This calls Spitch TTS for every script and language, validates each file, and
writes an updated manifest.

Flags:

- `--force` — regenerate all files even if they exist on disk.
- `--only <script_id>` — regenerate one script only (e.g. `--only welcome_consent`).
- `--clean` — list mp3 files that are not in the manifest (does not delete them).
- `--check` — run the startup check: log a warning for every script/language
  that has no audio file in the manifest, and for any manifest entry whose file
  is missing.

When a script's text changes, its hash changes, the file name changes, and the
old file becomes an orphan. Run `--clean` to see orphans.

## Audio format

Onboarding audio is mp3. The format is controlled by the environment variable
`ONBOARDING_AUDIO_FORMAT` (default `mp3`).

To switch to OGG/Opus later (so WhatsApp shows a voice-note waveform instead of
an audio file player):

1. Set `ONBOARDING_AUDIO_FORMAT=ogg`.
2. Add ffmpeg-based conversion at generation time in
   `app/voice/generate_assets.py` (convert the Spitch mp3 output to OGG/Opus).
3. Re-run the generator.

The `app/voice/audio_store.py` helper reads `ONBOARDING_AUDIO_FORMAT` and uses
the matching MIME type when uploading to WhatsApp, so only the generator and the
config need to change.

## Voice and speed

Voice choice and language mapping are in one place:
`app/services/text_to_speech.py`, in the `VOICES` dictionary. One voice per
language, the same voice for every script in that language.

## Deployment

The mp3 files are committed to the repository so they are present on every
deploy. Do not store them in the database.

## Startup check

The voice pipeline's startup check logs a warning for every script and language
that has no audio file. Run it with:

```bash
.venv/Scripts/python -m app.voice.generate_assets --check
```

## Missing audio

When a script/language has no audio file, the voice pipeline falls back to
sending the script's text as a normal WhatsApp message and logs an error. The
user is never left in silence.
