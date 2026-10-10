# TODO: Generate new voice notes

The original onboarding voice notes from `VOICE_SPEC.md` (section 7, "Source scripts") are already generated. Do not regenerate them. Add only the new scripts below, using the same pipeline, mp3 format, hash-keyed filenames and generate command as before (see `VOICE_SPEC.md` section 6.4, "Pre-generated, cached audio"). Existing files are skipped because their hashes have not changed.

## New scripts

| script_id | Languages | Source text |
|---|---|---|
| `diagnosis_prompt` | yoruba, hausa, igbo, pidgin, english | Please send a photo or voice note of your crop problem, and we will diagnose it for you. |
| `please_wait` | yoruba, hausa, igbo, pidgin, english | Please, wait. The response might take some time. |
| `feature_coming_soon` | yoruba, hausa, igbo, pidgin, english | This feature will be added in the nearest future. |

## Where they play

`diagnosis_prompt`: when the user chooses option 1 of the main menu ("Diagnosing a crop problem"), send this voice note in the user's stored language. Await the send so nothing else is sent before it finishes. This is the step after the main menu in `VOICE_SPEC.md` section 4, Step 4 (service menu).

`please_wait`: send it in the user's stored language as soon as the user's photo or voice note has been received and processing starts, before the diagnosis reply is ready. Send it once per request, not again if the processing is slow. Do not wait for it to finish before starting the processing; send it in the background so it does not delay the reply.

`feature_coming_soon`: send it in the user's stored language when the user chooses a main menu option whose feature is not built yet. Do not start any flow for that option, and keep the user's state unchanged so they can pick another option or send a new message.

## Steps

1. Add `diagnosis_prompt`, `please_wait` and `feature_coming_soon` to the script source file with the text above, alongside the script IDs from `VOICE_SPEC.md` section 7.
2. Run the existing generate command so it creates the fifteen new mp3 files (three scripts, five languages) and skips everything else.
3. Wire `diagnosis_prompt` into the menu option 1 handler, `please_wait` into the start of the diagnosis processing, and `feature_coming_soon` into the handler for each menu option that is not built yet, loading each cached file by script_id and the user's language.
4. If the cached file is missing, log it, generate it once, save it, and send it. Never leave the user in silence.
5. Add the new scripts to the startup check that logs missing assets.
6. When done, print what was generated and any failures.

## Review note

Have a native speaker listen to the Yoruba, Hausa, Igbo and Pidgin files before real users hear them.
