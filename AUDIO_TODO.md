# TODO: Pre-generated onboarding voice notes (mp3)

## Goal

Generate every fixed onboarding voice note once, in advance, for all five languages (English, Hausa, Igbo, Yoruba, Pidgin), save them as **mp3** files, and make the voice onboarding send those files instead of generating audio at run time.

Context: `VOICE_SPEC.md` describes the voice onboarding flow, and section 6.4 describes the audio caching. This TODO replaces the "cache as you go" idea with pre-generation. Follow `VOICE_SPEC.md` for everything not covered here.

Rules for the whole task:

- Work only in the `voice/` module, plus the shared helpers it already imports. Do not change the text onboarding.
- Use the existing Spitch TTS and Groq translation integrations. Do not add new TTS providers.
- No text-to-speech call may happen while a user is in the onboarding flow. All onboarding audio comes from files.
- Tick each box only when it works, and report anything unexpected.

## Phase 0: Read first

- [ ] Read `voice/script.py` and `VOICE_SPEC.md` section 7.
- [ ] Find the existing Spitch TTS code (check the text_to_speech.py file for this there is a function called "text_to_speech" ). Note which languages and voices it supports, what it returns (bytes, URL, file), and what audio format it outputs.
- [ ] Find the existing WhatsApp send helpers. Note how audio is sent today (upload then send by media ID, or by link).
- [ ] Find the existing storage layer and the deployment setup. Answer: store it in my local codebase
- [ ] Write a short note listing these findings before continuing.

## Phase 1: Complete the script source

Every fixed onboarding message needs a text version per language in one place.

this has already been done if you go to the voice folder inside the Script.py file  look at the SCRIPTS variable 

## Phase 2: Audio generator

Create `voice/generate_assets.py`, runnable as `python -m voice.generate_assets`.

- [ ] Loop over every script id and language in `SCRIPTS`(this is inside the voice/script.py) and the dynamic scripts from Phase 1.
- [ ] For each, build a file name: `{script_id}__{language}__{hash8}.mp3`, where `hash8` is the first 8 characters of a SHA-256 hash of the script text. Changing the text changes the name, so edited scripts are regenerated and unchanged ones are skipped.
- [ ] If that file already exists, skip it. Print "skipped".
- [ ] Otherwise call Spitch with the text, language and the voice configured for that language, and save the result as an **mp3** file in the audio folder.
- [ ] Put voice choice and speed in one config place (one voice per language, same voice for every script in that language).
- [ ] Add a `--force` flag that regenerates everything, and a `--only script_id` flag that regenerates one script.
- [ ] Write `voice/audio/manifest.json` after each run. It maps `script_id` and `language` to the current file name and its text hash, for example:
  ```json
  { "welcome_consent": { "yoruba": { "file": "welcome_consent__yoruba__a3f9c2d1.mp3", "hash": "a3f9c2d1" } } }
  ```
- [ ] Print a summary at the end: generated, skipped, failed (with the reason). Exit with an error if any file failed.
- [ ] Check each generated file: it exists, it is not empty, and its duration is plausible (more than 2 seconds, less than the WhatsApp limits). Flag any that fail.
- [ ] Add an `--clean` flag that lists (does not delete) old mp3 files that are no longer in the manifest.

## Phase 3: Storage

- [ ] Based on the Phase 0 answer, store the mp3 files in one of these (ask the owner if it is unclear):
  - a folder in the project, `voice/audio/` (commit the files to the repository so they are present on every deploy)
- [ ] Keep `manifest.json` in the same place as the audio files.
- [ ] Do not store the audio inside the main database.
- [ ] Run the generator once for all scripts and languages. Report the total number and size of files.

## Phase 4: Send helper

Create one helper in the voice module, for example `voice/audio_store.py`:

- [ ] `get_audio_file(script_id, language)`: reads `manifest.json`, returns the file for that script and language. If the exact language is missing, fall back to English. If there is no file at all, return nothing.
- [ ] `send_voice_script(phone, script_id, language)`:
  1. Gets the file.
  2. Uploads it to WhatsApp **once** and caches the returned media ID (in memory or in a small record), together with the upload time.
  3. Sends the audio by media ID.
  4. If the media ID is older than 25 days or the send fails because the ID is invalid, re-upload the file, update the cached ID and retry once.
  5. If there is no file, or the upload fails twice, send the script's **text** as a normal text message so the user is never left in silence, and log an error.
- [ ] Send it as an `audio/mpeg` message. Note: WhatsApp shows mp3 as an audio file with a player, not as a "voice note" with the microphone waveform, which needs OGG/Opus. Add a config flag `ONBOARDING_AUDIO_FORMAT` with the value `mp3` for now, and write the helper so switching to OGG/Opus later (converting with ffmpeg at generation time) only needs a config change and a regeneration.
- [ ] Make sure the audio is sent first and the interactive message is sent after it, awaiting each send in order.



## Phase 5: Checks

- [ ] A startup check logs a warning for every script and language that has no audio file in the manifest, and for any manifest entry whose file is missing.
- [ ] Test each onboarding step for each of the five languages and confirm the right file plays (listen to at least one per language).
- [ ] Test the fallbacks: remove one file and confirm the text message is sent instead and an error is logged; make a media ID invalid and confirm a re-upload happens.
- [ ] Confirm no TTS call happens during a full onboarding run (check the logs or mock Spitch to fail the test if it is called).
- [ ] Confirm the text onboarding flow is unchanged.
- [ ] Add a short `voice/README.md` explaining: how to edit a script, how to regenerate audio (`python -m voice.generate_assets`), where the files live, and how to switch format.

## Phase 6: Handover

- [ ] Report: files created, number of audio files per language, total size, any scripts still missing a language, and any step in Phase 5 that could not be replaced and why.
- [ ] Remind the owner: after native speakers review the texts, edit `voice/script.py` and rerun the generator. Only the changed scripts regenerate.
