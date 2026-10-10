# Spec: Voice-Note Onboarding for AjaTrade

## 1. Goal

AjaTrade already has a complete onboarding pipeline for users who message the bot by **text** (`OnboardingStateMachine`). This feature adds a parallel pipeline for users who **cannot read or write** and can only send **voice notes (VNs)**.

A first-time user whose first message is a VN is taken through: language detection, consent, role, then the service menu. Every instruction is delivered as a VN in the user's own language, with an interactive WhatsApp message beneath it.

Design assumption: the user cannot read the buttons. Every interactive message is paired with a VN that explains what each option means, and every interactive step can also be answered by voice (see 6.3).

## 2. Instructions for the implementing agent

The voice pipeline is a **separate implementation** from the text pipeline. The text onboarding is the reference, and the voice onboarding is a copy of it adapted to voice, so that the two behave the same way and write the same data.

Before writing code:

1. Read the existing text onboarding closely: `OnboardingStateMachine` and its methods (`get_or_create_user`, `restart`, `process`, `handle_new`, `handle_language`, `handle_consent`, `handle_role`, `handle_declined`, `handle_active`, `invalid_answer`, `normalize_input`), the `User` dataclass, the `LANGUAGES` and `ROLES` mappings, and the message builders (`language_question`, `create_language_message`, `consent_message`, `create_role_message`, `service_menu`, `text_message`, `restart_message`, `full_privacy_notice`, `GOODBYE_MESSAGES`).
2. Build the voice pipeline as its own module (for example `voice/`) with a class that mirrors the text class: same method names, same order of checks in `process`, same state transitions and the same storage calls (`storage.get_user`, `storage.save_user`). Change each step to use the voice mechanics in this spec.
3. **Do not modify the text pipeline's files**, except for the minimal change in the webhook handler that sends audio messages to the voice pipeline. The text flow must behave exactly as before.
4. Do not import onboarding handlers from the text pipeline into the voice pipeline. Utilities that are not onboarding logic may be imported and shared: the WhatsApp send and download helpers, the Groq client, the translation and Spitch TTS pipeline, the storage layer, and the message-content definitions (`LANGUAGES`, `ROLES`, `GOODBYE_MESSAGES`, `full_privacy_notice`, the message builders). Sharing the content definitions keeps button IDs and wording identical across both flows.
5. Use the same `User` record, the same storage and the same state names and values as the text flow. Do not create a second collection or a different set of states. A user may start with a VN and later send text, or the reverse, and both pipelines must agree on where the user is.
6. Report your findings in a short note before implementing: the values stored in `LANGUAGES` and `ROLES`, the button IDs, and any inconsistencies you found.

Where this spec conflicts with how the existing code works, prefer the existing code and flag the conflict.

## 3. Languages

The classifier returns short codes. The text flow stores a different value in `user.language`. The voice pipeline must store **exactly what the text flow stores** for the same language.

| Classifier code | Language | Value stored in `user.language` |
|---|---|---|
| yo | Yoruba | the value `LANGUAGES` produces for Yoruba (the owner says the text flow stores full names such as `yoruba`) |
| ha | Hausa | same, for Hausa |
| ig | Igbo | same, for Igbo |
| pcm | Nigerian Pidgin | same, for Pidgin |
| en | English | same, for English |
| other | anything else | not stored; this is the unsupported path |

Implement one mapping function, `classifier_code_to_stored_language(code)`, built from the `LANGUAGES` mapping, so the voice module never hardcodes language strings.

Known inconsistency to check and report: in `handle_language` the typed-number branch assigns short codes (`"ha"`, `"yo"`, `"ig"`, `"pcm"`, `"en"`), while other methods fall back to `"english"` or `"en"`, and the owner says the stored values are full names. Read `LANGUAGES` and confirm what an interactive list selection actually writes. Voice must write that same value.

## 4. User-facing flow

### Trigger

An audio message arrives from a phone number. `get_or_create_user` creates the record if it does not exist (state `new`). The content of the first VN does not matter: whatever was said, a first VN starts onboarding.

### Step 1: Language detection (replaces the language list)

The text flow asks the user to pick a language from a list. The voice flow detects it.

On `new`: set `state = "awaiting_language"` and save, then:

### Step 1: Language detection

1. Download the VN from WhatsApp (media URLs are short-lived and need the access token) (look at the code base inside the whatsapp folder the get_media file ).
2. Transcribe it with the whisper speech-to-text model already configured in the project
3.  create a function inside the @translation.py called language_detector, that calls the _chat function and that inserts this system prompt (LANGUAGE_DETECTION_SYSTEM_PROMPT).
3. The function return this sort of JSON It returns strict JSON:
   ```json
   { "language": "yoruba|hausa|igbo|pidgin|english|other", "confidence": 0.0, "detected_other": "free text or null" }
   ```
save on the user record: if language confidence is 0.7 upwards save the language of the user inside the language table of the database and  `user.language_source = "detected"`, `user.language_attempts = 0`, `state = "awaiting_consent"`, save, and go to Step 2.

Do not store the transcript or the audio. Log the detected code and the confidence (not the transcript) for debugging.

### Low-confidence path

Track failures in `user.language_attempts`.

- First failure: set `language_attempts = 1`, send a VN in English and Pidgin asking them to send another VN, speaking slowly in a quiet place (`low_confidence_retry`). State stays `awaiting_language`.
- Second failure: default to Pidgin. Set `language` to the stored Pidgin value, `language_source = "default_fallback"`, `language_attempts = 0`, `state = "awaiting_consent"`. Send `pidgin_default_notice` in Pidgin, then continue straight to Step 2 in Pidgin. Do not show a language list.
<!-- - A later VN from a user with `language_source == "default_fallback"` that is detected as a supported language above the threshold updates `language` to it and sets `language_source = "detected"`. -->

<!-- ### Unsupported path

When `language == "other"`:

- State stays `awaiting_language`. Do not add a new state.
- Send one VN in English (`unsupported_language`), then nothing else. No follow-ups or reminders.
- Set `user.unsupported_notified_at` to now. If `unsupported_notified_at` is within the last 24 hours, store nothing new and send no reply (this stops a loop).
- Log `detected_other` and a timestamp through the existing logger so you can see which languages users ask for. Do not store the transcript.
- If the same number sends another VN after the window, run detection again from Step 1. A user who retries in a supported language proceeds normally. -->

### Step 2: Welcome and consent (`awaiting_consent`)

Send, in this order, awaiting each send before the next so ordering is preserved:

1. A VN in the user's language (`welcome_consent`): what AjaTrade is, that we would like to save their chat and phone number, and what each of( use the get__welcoming_script function to get the text just make sure to insert the language).
2. The existing `consent_message(language)` interactive message, with the existing button IDs: `consent_yes`, `consent_no`, `consent_read_more`.

Handle the response exactly as the text flow does:

- `consent_yes`: `consent_status = "granted"`, `consent_at = now (UTC)`, `consent_version = "v1"`, `state = "awaiting_role"`, save. Send `consent_accepted_ack` followed by the role prompt VN and `create_role_message(language)` (Step 3).
- `consent_no`: `consent_status = "declined"`, `state = "declined"`, Same as the text flow: no chat history or memory saved.
- `consent_read_more`: send a VN of `full_privacy_notice(language)`, then repeat Step 2 (the welcome VN is not repeated, only the consent prompt VN and `consent_message`). State does not change.
- Anything else: copy the behavior of `invalid_answer` for voice (see 6.3).

### Step 3: Role (`awaiting_role`)

Send, in order:

1. A VN in the user's language (`role_prompt`) explaining each option in the same order as the existing `ROLES` and `create_role_message` (press 1 for the first role, 2 for the second, 3 for the third). Generate the option wording from `ROLES`, so it can never disagree with the buttons.
2. The existing `create_role_message(language)` interactive message.

On a valid answer in `ROLES`: set `user.role`, `state = "active"`, save, then Step 4.

### Step 4: Service menu

Send, in order:

1. A VN in the user's language (`menu_intro`) introducing the menu and explaining each number, starting with "press 1 to get help diagnosing a crop problem". The other items are generated from the menu definition in `service_menu`, in the same order.
2. The existing `service_menu(language, name=user.preferred_name or user.name)` interactive message.

The text flow sends the service menu right after the role is chosen. The voice flow does the same.

### Users who are already `active` or `declined`

- **`active`:** transcribe the VN and pass the transcript to the same place the text flow passes its text (`handle_active`, currently a placeholder for the main AjaTrade pipeline). The voice module has its own copy of `handle_active` that mirrors the placeholder and is wired to the main pipeline when that exists. The menu is not resent here; the text flow only sends it after the role step and after a restart.
- **`declined`:** transcribe the VN. If the transcript is an affirmative ("yes" or its equivalent in the user's language), run `restart` and send `restart_prompt`. Otherwise send the VN version of `restart_message(language)`.
- **Global restart:** the text flow restarts onboarding on the exact messages "menu" or "start over". For voice, apply the same check to the whole transcript after normalizing (lowercase, trimmed, punctuation removed). Because a transcript is rarely exactly one word, this will seldom trigger; that is acceptable.

### Restart

Copy `restart()` exactly. It resets `state`, `language`, `consent_status`, `consent_at`, `consent_version`, `role`, `name` and `preferred_name`. The voice copy also resets `language_source`, `language_attempts` and `unsupported_notified_at`. After a voice restart, send `restart_prompt` asking the user to send a new VN; capture the old `language` before resetting it so the prompt can be sent in that language.

## 5. State machine

The voice flow uses the text flow's states only.

```
new
 └─ first VN ─► awaiting_language
      ├─ unsupported ─────────► awaiting_language (one English VN, 24h cap)
      ├─ low confidence x2 ───► (default Pidgin) awaiting_consent
      └─ language saved ──────► awaiting_consent
                                  ├─ consent_yes ─► awaiting_role ─► (role chosen) ─► active
                                  ├─ consent_no ──► declined
                                  └─ consent_read_more ─► awaiting_consent (unchanged)
declined ─ "yes" ─► restart ─► new
```

An interactive message is only handled when the user's state expects it. A stale button tap from an earlier step must not change state; replay the current step instead.

## 6. Cross-cutting requirements

### 6.1 Audio format

VNs sent to the user must be OGG/Opus so WhatsApp displays them as voice notes rather than audio file attachments. Confirm the TTS output format and convert with ffmpeg if needed. Set the voice flag if the send API requires it.

### 6.2 Interactive message limits

- Reply buttons: maximum 3 buttons, title maximum 20 characters.
- List messages: maximum 10 rows, row title maximum 24 characters.
- Body text maximum 1024 characters.

Use the existing builders and their button IDs unchanged. If you copy a builder into the voice module to put the number first in each label (`1 Accept`), keep the IDs identical and check the labels against these limits when assets are generated. Never truncate silently.

### 6.3 Voice replies to interactive steps

A user who cannot read may answer by VN instead of tapping. In `awaiting_consent` and `awaiting_role`, when an audio message arrives:

1. Transcribe it.
2. Use the LLM to map it to one valid option for that step, giving it the option IDs and the user's language: consent maps to `consent_yes`, `consent_no` or `consent_read_more`; role maps to one of the `ROLES` IDs ("one", "I am a farmer", "yes", "I agree", "tell me more").
3. If it maps with enough confidence, treat it exactly as if the button was tapped.
4. If not, do what `invalid_answer` does for text: replay the current step's VN with `step_replay_unclear` before it, and the interactive message again. State does not change. After two failed replays, send nothing more and wait.

### 6.4 Pre-generated, cached audio

Onboarding scripts are fixed text. Do not generate audio per user.

- Generate each script once per language through the existing translation and TTS pipeline, store the audio file in the existing storage, and key it by `script_id + language + hash(source_text)`. Changing the source text changes the hash and regenerates the audio.
- Scripts derived from the existing code (`role_prompt`, `menu_intro`, the goodbye VN, the privacy notice) take their source text from `ROLES`, `service_menu`, `GOODBYE_MESSAGES` and `full_privacy_notice`. If those texts change, the hash changes and the audio is regenerated.
- The text flow personalizes with the WhatsApp profile name. Cached VNs cannot include the user's name, so they omit it. The interactive menu message still uses the name.
- WhatsApp media IDs expire (about 30 days). If a send fails because a media ID is invalid, re-upload from the stored file and retry.
- Provide a command that (re)generates all onboarding assets, and a startup check that logs which assets are missing.

### 6.5 Reliability

- Deduplicate incoming webhook events by WhatsApp message ID. A duplicate delivery must not send a second welcome flow.
- If transcription or classification fails with an API error, do not leave the user in silence. Treat it as a low-confidence result, log the error and do not advance state.
- Log each state transition: phone number (hashed if the project already does this), old state, new state and message ID.
- Update `created_at` and `last_seen_at` in the same places the text flow does.

### 6.6 Data and consent

The voice flow reads and writes the same `User` record as the text flow. Fields it uses: `phone`, `name`, `preferred_name`, `state`, `language`, `role`, `consent_status`, `consent_at`, `consent_version`, `created_at`, `last_seen_at`.

**New optional fields.** Add these to the `User` dataclass with defaults so the text flow is unaffected, and make sure the storage layer persists them:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `language_source` | `str \| None` | `None` | `"detected"` or `"default_fallback"` |
| `language_attempts` | `int` | `0` | counts low-confidence failures |
| `unsupported_notified_at` | `datetime \| None` | `None` | 24-hour cap on the unsupported reply |

**Declined users.** Same as the text flow: only what is needed to run the service is kept (phone number, `language`, `consent_status = "declined"`, `state = "declined"`). No role, no chat history, no memory, no transcript and no audio.

**Transcripts and audio.** The voice flow does not persist transcripts or audio of onboarding messages. Chat messages are not saved by the text flow yet; when message saving is built, it must only save for users with `consent_status == "granted"`, for voice and text alike.

## 7. Source scripts (English)

Source texts for the translation pipeline. Run each through translation and TTS per language. Have a native speaker review each language before launch, especially Yoruba, Hausa, Igbo and Pidgin. Keep sentences short and simple; these are heard once, possibly on a poor connection.

**`unsupported_language`** (English only)
> Hello, thank you for your message. AjaTrade works in Yoruba, Hausa, Igbo, Nigerian Pidgin and English. We could not understand the language you used. Please send us another voice note in one of these languages, and we will be happy to help.

**`low_confidence_retry`** (English and Pidgin)
> Sorry, we could not hear you well. Please send another voice note. Speak slowly and stay in a quiet place.

**`pidgin_default_notice`** (Pidgin)
> Sorry, we could not understand your language well. We will talk to you in Pidgin. Let us continue.

**`welcome_consent`**
> Welcome to AjaTrade. AjaTrade is a helper on WhatsApp for farmers and traders. You can send us a voice note about your farm or your business, and we will answer you in your language. To serve you well, we would like to save your phone number and our chat. Below this message there are three buttons. Press number 1 if you agree. Press number 2 if you do not agree. Press number 3 if you want to hear more about how we use your information. You can also just say "I agree", "I do not agree" or "tell me more" in a voice note.

**`consent_accepted_ack`** (added at the start of the role VN)
> Thank you. We will save your chat and your phone number.

**`role_prompt`**
> Now, tell us what describes you. Below this message there are three buttons. [Generated from `ROLES` in the same order as the buttons: "Press number 1 if you are a ...", "Press number 2 if you are a ...", "Press number 3 if you are a ..."] You can also say your choice in a voice note.

**`menu_intro`**
> You are all set. Below this message is our menu. Press number 1 if you want help to find out what is wrong with your crop. [Then, for each remaining item in `service_menu`, in order: "Press number N for ..."]

**Goodbye VN:** generated from `GOODBYE_MESSAGES` in the user's language.

**Privacy notice VN:** generated from `full_privacy_notice(language)`.

**`step_replay_unclear`**
> Sorry, we did not understand. Please press one of the buttons below, or say your choice in a voice note.

**`restart_prompt`**
> Okay, let us start again. Please send us a voice note in the language you speak.

## 8. Acceptance criteria

1. A new number sends a VN in Yoruba, Hausa, Igbo, Pidgin or English: the record's `language` equals the value the text flow would store for that language, `state` is `awaiting_consent`, and the welcome VN plays in that language followed by the consent buttons.
2. A new number sends a VN in an unsupported language (for example Tiv or French): the English VN is sent once, state stays `awaiting_language`, no other messages are sent, and `unsupported_notified_at` is set.
3. The same number then sends a VN in Hausa after the 24-hour window, or sooner (the cap only limits replies, not detection): onboarding continues normally.
4. A silent or very short VN gets the retry VN. A second failure defaults the user to Pidgin (`language_source = "default_fallback"`), sends the Pidgin notice VN, and continues to consent in Pidgin.
5. `consent_yes` stores `granted`, `consent_at` and `consent_version = "v1"`, moves to `awaiting_role` and sends the role VN and buttons.
6. `consent_no` stores `declined`, sets `state = "declined"`, sends the goodbye VN and nothing else, with no role step and nothing stored beyond the allowed fields.
7. `consent_read_more` sends the privacy notice VN and repeats the consent prompt without changing state.
8. A valid role choice stores `role`, sets `state = "active"` and sends the menu VN and `service_menu`.
9. Replying to consent or role by VN ("yes", "one", "I am a farmer", "tell me more") behaves the same as tapping.
10. A declined user who sends a VN meaning "yes" is restarted and receives the restart prompt.
11. A VN user can later send text (and the reverse) and the text flow continues from the same state with the same record.
12. A duplicate webhook delivery of the same message ID causes no duplicate messages.
13. The text onboarding flow behaves exactly as before, and no text pipeline files were changed apart from the webhook branch for audio.
14. All cached audio exists for all five languages, and button labels are within WhatsApp limits.

## 9. Implementation checklist

- [ ] Read and summarize the text flow: states, `User` fields, `LANGUAGES`, `ROLES`, button IDs and message builders; report inconsistencies, especially the stored language values
- [ ] Create the separate voice module with a class mirroring `OnboardingStateMachine`
- [ ] Add the audio branch to the webhook that hands VNs to the voice module (the only change outside it), with media download and dedupe by message ID
- [ ] Add the three optional fields to `User` and confirm the storage layer persists them
- [ ] Implement transcription with the configured Groq STT
- [ ] Implement the language classifier call, response validation and the confidence threshold in config
- [ ] Implement `classifier_code_to_stored_language` from `LANGUAGES`
- [ ] Implement the low-confidence path with the Pidgin default and later language correction
- [ ] Implement the unsupported path with the 24-hour reply cap
- [ ] Implement consent handling (`consent_yes`, `consent_no`, `consent_read_more`) with the text flow's stored values
- [ ] Implement role handling and the service menu step using `ROLES` and `service_menu`
- [ ] Implement `handle_active`, `handle_declined`, the global restart check and `restart` for voice
- [ ] Create the script source file with all script IDs from section 7
- [ ] Generate translations and TTS audio for all five languages, convert to OGG/Opus and cache with hash keys
- [ ] Validate button labels against WhatsApp length limits
- [ ] Implement send helpers: VN then interactive message, awaited in order
- [ ] Implement voice replies to interactive steps (6.3) and the replay behavior
- [ ] Add transition logging and error fallbacks (6.5)
- [ ] Write tests or a manual test script covering the 14 acceptance criteria
- [ ] Native-speaker review of all translated scripts before launch