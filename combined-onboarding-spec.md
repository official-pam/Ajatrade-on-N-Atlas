# AjaTrade: combined voice + text onboarding (prototype spec)

Audience: a coding agent working in the AjaTrade repo.
Deadline: working prototype tomorrow. Prefer the smallest change that works. Do not refactor.

## 1. Decision (already made, do not revisit)

The voice and text onboarding flows are combined for the prototype. At every onboarding step the user receives **both**:

1. the pre-generated voice note explaining the step (sent first), then
2. the text message and interactive buttons (sent after).

The reason: a button tap arrives as `type: "interactive"` and was falling into the text pipeline, so the next voice note never fired. After this change, every path sends both, so it no longer matters which pipeline handles the tap.

## 2. Before you write any code

Read these files first and confirm what is in them:

- `app/onboarding/state_machine.py` (`OnboardingStateMachine`, the text flow)
- the voice onboarding module (`VoiceOnboarding`, the voice flow)
- `app/api/routes/webhook.py` (message routing and sending)
- `app/voice/audio_store.py` (`default_store`, `send_voice_script`, `check_missing`)
- `app/voice/scripts.py` and `app/onboarding/messages.py`

If the webhook or the audio store differs from what this spec assumes, stop and tell me what you found instead of guessing.

## 3. Do not touch

- the farmer translation pipeline and TTS code
- the user record schema (`app/schemas/memory.py`) and the stored field values
- the text flow's states and stored values. Both flows must keep writing identical data.

## 4. How the two flows differ today (this is intentional)

**Language step:**

- **Text flow:** the user chooses from a **menu** (`create_language_message`, a list). `handle_language` maps the tapped id through `LANGUAGES`.
- **Voice flow:** nothing is shown. The voice note is downloaded, transcribed, and **classified by the language detector**. The result is checked against `LANGUAGE_CONFIDENCE_THRESHOLD` (0.7).

Keep both. The entry point decides which one runs:

- First message is a voice note: use detection.
- First message is text: use the menu.

From `awaiting_consent` onward the two paths are one flow.

Low-confidence detection stays as specced: first failure, ask for another voice note; second failure, default to Pidgin and continue to consent.

## 5. Target behaviour

| State | Voice note sent | Then text / interactive |
|---|---|---|
| `awaiting_language` (text entry) | none unless a script exists | language menu |
| `awaiting_consent` | `welcome_consent` | consent buttons |
| consent "read more" | `privacy_notice` | consent buttons again |
| `awaiting_role` | the role-explainer note (1 farmer, 2 trader, 3 other), if it exists in the audio store | role buttons |
| after role, `active` | none | main menu |
| consent declined | `goodbye` (see section 8, decision 1) | per the text flow |

Rules:

1. **Order.** Await the voice send before returning or sending the text and buttons.
2. **Voice never blocks text.** Wrap every voice send in try/except and log it. If it fails or the script is missing, send the text and buttons anyway. Send the text fallback of the script too when the audio fails.
3. **No duplicate logic.** Put the "voice note, then messages" step in one helper that both flows call. Do not copy the step logic into two places.
4. **One source of truth for state.** State transitions and stored values come from the text `OnboardingStateMachine`. The voice side may only decide *which answer id* a voice note means, then hand it to the same handler.
5. **Voice answers map to the same ids** the buttons use: `consent_yes`, `consent_no`, `consent_read_more`, and the role ids from `ROLES`.
6. **Dedupe.** Skip a webhook message whose `message["id"]` was already processed, so a Meta retry cannot send a voice note twice.
7. **Respond 200 to Meta.** Wrap the send calls in the webhook so a WhatsApp send error is logged and never becomes a 500.

## 6. Known bugs in the voice module (fix these as part of the work)

1. **`handle_language` is stubbed.** The real detection block is commented out and it ends with `return await self.consent_prompt('09067631070', "hausa")`, a hardcoded debug call. Restore the detection path. It must also save `user.state = "awaiting_consent"` (that line is commented out) and use the real `user.phone`.
2. **Classifier value mismatch.** The language detector now returns full lowercase names (`yoruba`, `hausa`, `igbo`, `pidgin`, `english`, `other`). `CLASSIFIER_CODE_TO_OPTION_ID` and `classifier_code_to_stored_language` still expect `yo`, `ha`, `pcm` and are never called. Map the classifier output to the exact value the text flow stores (check `LANGUAGES` in `messages.py`; confirm what is stored for Pidgin). `other` goes to the unsupported-language path.
<!-- 3. **`replay_consent` and `low_confidence` are not methods.** They are indented under `_detect_consent_answer` after its `return None`, so they are unreachable nested functions and `VoiceOnboarding` does not have them. Move them back into the class. -->
4. **`_consent_replays` is never created.** Add `self._consent_replays: dict[str, int] = {}` to `__init__`.
5. **Wrong argument in `process`.** `handle_consent(user, message)` receives the raw message dict, but it expects an answer id. Use `await self.detect_consent(message, user.language)` (currently commented out) when the message is audio, and the interactive id when it is a button tap.
6. **`consent_prompt` returns a dict, callers expect a list.** `replay_consent` and `low_confidence` do `*await self.consent_prompt(...)`, which would splat the dict's keys. Always return a list.
7. **Missing or misspelled imports.** `LOW_CONFIDENCE_RETRY` is used but not imported. `get__welcoming_script` has a double underscore. Confirm the real name in `scripts.py`.
8. **`grant_consent` has the `consent_ack` voice note commented out.** Restore it, with the text fallback.
9. **`_detect_consent_answer` is English-only.** Acceptable for the prototype, but button taps are the reliable path. Do not spend time here.

Text flow, minor (fix only if trivial, otherwise report): `invalid_answer` uses a shallow `.copy()` and then edits nested dicts, which can make the instruction text pile up on shared message templates. `handle_declined` calls `answer.lower()` and crashes when `answer` is `None`.

<!-- ## 7. WhatsApp limits to respect

A recent 400 error (`131009`, "Button title length invalid, min 1, max 20") came from a button title being empty or too long. Every interactive payload must pass these checks before sending:

- button title: 1 to 20 characters. Normalize with `unicodedata.normalize("NFC", ...)` before counting, since Yoruba, Igbo and Hausa letters can use combining marks.
- maximum 3 reply buttons per message
- list row title: up to 24 characters
- interactive body text: up to 1024 characters
- plain text body: up to 4096 characters, never empty

Add a small `safe_title(text, fallback)` helper that strips, falls back if empty, and truncates to 20. Log the payload when a send fails (`response.is_error`), including the `error.code` and `error.message`. -->

## 7. Decisions needed from the owner

If you cannot find the answer in the code, ask me. Do not choose silently.

1. **Declined consent.** The text flow sends a declined user on to `awaiting_role` (stores `consent_status = "declined"`). The voice flow instead sets `state = "declined"` and sends `goodbye`. Default for this prototype: **follow the text flow**, so both paths behave the same. Tell me if the voice goodbye should stay.
2. **Role voice note.** Is there an audio script for the role step? If not, skip the voice note for that step and report it, do not generate one without asking.
3. **Pidgin stored value.** `consent_message` raises `KeyError` for some stored values (Pidgin, Hausa with trailing spaces). The voice code currently falls back to English for the buttons. Report which languages hit the fallback.

## 9. Test checklist (run all before saying it is done)

- [ ] First message is a **voice note** in Yoruba: language detected, welcome voice note arrives, **then** consent buttons.
- [ ] First message is **text**: language menu, pick a language, welcome voice note, then consent buttons.
- [ ] Tap **I agree**: consent voice note (or text fallback), then role buttons.
- [ ] Tap **Read more**: privacy voice note, then consent buttons again, state unchanged.
- [ ] Tap **Decline**: behaves per decision 1 and stores only what is allowed.
- [ ] Tap a **role**: state becomes `active`, main menu sent.
- [ ] Say "yes" by **voice** at consent: same result as tapping I agree.
- [ ] Send **unclear audio** twice at consent: replays, then goes quiet (no third reply).
- [ ] **Unsupported language** voice note: English voice note saying only the three Nigerian languages are supported.
- [ ] **Low confidence** twice: user defaults to Pidgin and continues.
- [ ] Break the voice send on purpose: text and buttons still arrive.
- [ ] Send the same webhook payload twice: only one set of messages goes out.
- [ ] No hardcoded phone numbers or test values remain (`grep` for `09067631070`).

## 10. Report back

When finished, list: files changed, which bugs from section 6 were fixed, anything skipped and why, and the answers or defaults used for section 8.
