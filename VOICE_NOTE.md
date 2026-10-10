# AGENTS.md: AjaTrade post-onboarding flow

This file tells you what to build and how to behave while building it. Read all of it before writing code.

## 0. How to work

1. Read the codebase first (section 2). Do not assume file contents, function names or table layouts. If something here conflicts with what the code actually does, the code wins; report the difference.
2. Before writing any code, post a short plan and a list of questions (section 14 lists the ones already known). Wait for the owner's answers to blocking questions.
3. Work in the phases in section 13, one at a time. Keep each phase small enough to review.
4. Do not change onboarding behaviour. Onboarding ends when a user becomes `active` with consent accepted; everything in this file applies only after that.
5. Reuse existing functions (STT, translation, TTS, `send_voice_note`, interactive message senders). Do not write second versions of them.
6. If you add or change anything in `storage/model.py`, update the matching schemas file in the same folder in the same change.
7. Never hardcode API keys, model names or phone numbers. Read them from the existing config or environment handling.
8. Match the existing code style, async or sync convention, and logging approach.
9. Add tests for the pure logic (section 11). Do not call real APIs in tests.

## 1. Product context

AjaTrade is a WhatsApp assistant for smallholder farmers in Nigeria. Most users cannot read or write, so every reply to the user must be a voice note. Text is only used for the interactive menu, which has a spoken counterpart.

Supported languages: English, Yoruba, Hausa, Igbo, Nigerian Pidgin. Internally all reasoning happens in English. The pipeline for every outgoing reply is:

```
English text -> Groq translation to the user's saved language -> Spitch TTS -> send_voice_note
```

Incoming pipeline for voice: Whisper STT -> Groq translation to English.

The menu has three services: `crop_detection`, `market_price`, `connect_vendor`. This file implements `crop_detection` fully. The other two get routing stubs only (section 9).

## 2. Read these first

File names are as the owner typed them and may contain typos. Search the repo to find the real paths.

- `webhook.py`: entry point for incoming WhatsApp events, and how onboarding is invoked.
- `onboarding/message.py`: contains the `service_menu` function.
- `diagnosis/disease_diagonis.py`: existing photo diagnosis (vision model call and system prompt).
- `service/whatsapp/messasges.py`: WhatsApp message handling. There is currently no text-conversation code or model; you will create it.
- `send_media.py`: contains `send_voice_note`.
- `whatsapp_onboarding.py`: contains the interactive message sender.
- `storage/model.py` and the schemas file beside it: database models.

Report back, in your plan: how the webhook currently dispatches by message type, whether processing is already done in a background task, how user state is read and written, and which migration tool (if any) the project uses.

## 3. Decisions already made

| Topic | Decision |
|---|---|
| Reply format | Voice notes only, in the user's saved language |
| Photo | Optional. A voice description gets a cautious best-guess diagnosis; a photo is requested at the end for confirmation |
| Open case timeout | 3 hours of inactivity, checked lazily when the next message arrives |
| Follow-up questions | Maximum 2 per case |
| Topic switch mid-case | Switch immediately |
| Photo with no open case | Start a new diagnosis |
| Long-term memory | Include the `conversation_memory` summary now |
| "Please wait" voice note | Wanted, but must be pregenerated audio. Leave a no-op hook now (section 12) |
| Vision model | `qwen/qwen3.8-27b` (read the model name from existing config, do not hardcode) |
| Chemicals | The model may name a product type or active ingredient. It must never give doses, mixing amounts or spray schedules |
| Re-diagnosis | While a case is open, an extra voice note with new detail re-runs the text diagnosis, up to 2 diagnoses per case. A photo diagnosis always closes the case |

## 4. Message routing

Applies to users who are `active` with consent accepted. For every incoming message:

1. Deduplicate by WhatsApp message id (webhooks retry). If already stored, ignore.
2. Store the incoming message (section 6).
3. Expiry check: if `flow` is set and `now - last_activity_at > 3 hours`, close the case (section 8) and treat this message as having no open case.
4. Route by type:

| Incoming | Rule |
|---|---|
| Interactive list reply | The row id (`crop_detection`, `market_price`, `connect_vendor`) sets `flow` and starts that flow immediately, even if another flow was open |
| Photo | Run photo diagnosis (section 7). If no case is open, start one first |
| Voice or text, `flow == crop_detection` | Continue the case (section 8) |
| Voice or text, no open flow | Classify intent with the text model: `crop_problem`, `market_price`, `find_vendor`, `greeting`, `unclear`. `crop_problem` starts a case. `greeting` or `unclear` sends a short welcome-back voice note plus the menu from `service_menu` |

A user who has not messaged for a long time gets the same welcome-back plus menu on their first message back.

Do not send the full menu with every reply. The menu is a fallback for when intent is unclear.

If processing any step fails, send a short fixed voice note ("Sorry, something went wrong. Please try again."), log the error, and make sure `flow` and `step` are not left stuck in a state that cannot progress.

## 5. State fields

Add these to the user record (or a small related table if the user model should stay untouched; decide and say which in your plan). All must be persisted in the database, not held in memory.

| Field | Meaning |
|---|---|
| `flow` | `crop_detection`, `market_price`, `connect_vendor`, or null |
| `step` | What the bot is waiting for. For crop detection: `awaiting_crop`, `awaiting_description`, `awaiting_photo_optional`, or null |
| `current_case_id` | UUID of the open case, or null |
| `case_started_at` | When the open case began |
| `last_activity_at` | Time of the last message in the open case |
| `case_crop`, `case_part` | Facts learned so far, nullable |
| `case_symptoms` | JSON list of symptoms the farmer described |
| `questions_asked` | Follow-up questions asked in this case (cap 2) |
| `diagnoses_in_case` | Diagnoses sent in this case (cap 2) |

`flow` and `step` answer one question: when the next message arrives, what is it a reply to. Set `step` whenever you ask a question. Clear all case fields when a case closes.

## 6. Storage

The project currently stores nothing: no chat messages and no diagnoses. Create the tables below, update the schemas file, and add migrations if a migration tool exists.

### `messages`

| Field | Notes |
|---|---|
| `id` | primary key |
| `wa_id` | indexed |
| `whatsapp_message_id` | unique, for deduplication; null for bot messages if not applicable |
| `role` | `farmer` or `bot` |
| `kind` | `text`, `voice`, `image`, `interactive` |
| `original_text` | transcription or text in the user's language, nullable |
| `english_text` | English version, nullable |
| `language` | user's language code |
| `media_ref` | WhatsApp media id or storage reference, nullable |
| `case_id` | nullable; links the message to a case |
| `created_at` | indexed with `wa_id` |

Store both farmer and bot messages. Store the English text of bot replies (what you generated before translation).

### `diagnosis_log`

One row per diagnosis sent.

| Field | Notes |
|---|---|
| `id` | primary key |
| `case_id`, `wa_id`, `created_at` | |
| `source` | `photo` or `description` |
| `model_name` | which model produced it |
| `photo_ref` | nullable; see open question on photo storage |
| `raw_output` | full model output as JSON or text |
| `crop`, `diagnosis_name`, `category`, `confidence` | extracted for querying |
| `flags` | JSON list from `build_reply` |
| `reply_text_en` | what was actually sent (English, before translation) |
| `confirmed_diagnosis` | nullable, for later expert confirmation |

Only users who accepted consent reach this code, so logging is allowed. Do not log anything for users who declined consent.

### `conversation_memory`

| Field | Notes |
|---|---|
| `wa_id` | primary key |
| `summary` | short rolling summary: crops, location (town or LGA), past problems and advice, open follow-ups. Only things the user shared |
| `updated_at` | |

## 7. Photo diagnosis

Refactor the existing function in `disease_diagonis.py` rather than writing a new one.

Input to the vision model: the photo, plus a short context block built from the case. Nothing else.

```python
user_text = (
    "Context from the farmer (may be incomplete):\n"
    f"- Crop: {case.crop or 'not stated'}\n"
    f"- Part affected: {case.part or 'not stated'}\n"
    f"- What the farmer described: {'; '.join(case.symptoms) or 'nothing'}\n"
    "Judge mainly from the photo."
)
```

Do not send: the chat history, any earlier bot diagnosis (it anchors the model), the `conversation_memory` summary, the farmer's name or phone number.

The model's raw output goes through `build_reply` (below). Send `Reply.text_en` through the voice pipeline. Save a `diagnosis_log` row. Then close the case.

Use the provider's JSON mode if available, but still parse defensively.

### `build_reply`

Put this in a pure module with no network calls so it can be unit tested. The decision order matters.

```python
import json
import re
from dataclasses import dataclass, field

MSG = {
    "parse_error": "Sorry, I could not look at your photo well. Please send the photo again.",
    "not_plant": "I cannot see a crop in this photo. Please take a photo of the leaf or plant that has the problem.",
    "unclear": ("The photo is not clear enough. Stand close to the plant. "
                "Hold the phone steady, use good daylight, and send one clear photo of the sick leaf."),
    "healthy": ("Good news. Your crop looks healthy. Keep checking it often. "
                "If you see spots or yellow leaves later, send me another photo."),
    "unsure": ("I am not sure what is wrong with this plant. "
               "Please show it to an extension officer or an agro-dealer near you. "
               "You can also send me a photo of another leaf."),
    "extension": "Please also show the plant to an extension officer.",
    "medium_prefix": "I am not fully sure, but this is what I see.",
    "closing": "If you have another problem, send me another photo.",
}

# Safety net: catches doses, mixing amounts and spray schedules
DOSE_PATTERN = re.compile(
    r"\b\d+(\.\d+)?\s*(ml|millilit\w*|l|litres?|liters?|g|grams?|kg|tablespoons?|"
    r"teaspoons?|tbsp|tsp|cups?|caps?|sachets?)\b"
    r"|\bper\s+(litre|liter|knapsack|hectare|acre)\b"
    r"|\b(every|each)\s+\d+\s+days?\b",
    re.I,
)
MAX_WORDS = 80


@dataclass
class Reply:
    kind: str                  # error | retake | unsure | healthy | diagnosis
    text_en: str               # translate, TTS, send
    flags: list = field(default_factory=list)
    parsed: dict | None = None


def parse_model_output(raw):
    """Parse JSON even if wrapped in code fences or extra text."""
    if not raw or not isinstance(raw, str):
        return None
    text = re.sub(r"```(?:json)?", "", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean(text):
    """Return text only if non-empty and free of doses."""
    if not isinstance(text, str) or not text.strip():
        return None
    if DOSE_PATTERN.search(text):
        return None
    return text.strip()


def _build_fallback(data, name, confidence):
    crop = (data.get("crop") or {}).get("name") or "crop"
    verb = "has" if confidence == "high" else "may have"
    parts = [f"Your {crop} {verb} {name}."]
    actions = data.get("recommended_actions") or []
    safe = [a for a in actions if _clean(a)][:2]
    parts.extend(a.rstrip(".") + "." for a in safe)
    return " ".join(parts)


def build_reply(raw):
    data = parse_model_output(raw)
    if data is None:
        return Reply("error", MSG["parse_error"], ["parse_failed"])

    status = data.get("image_status")
    quality = data.get("photo_quality") or {}
    if status != "ok" or quality.get("usable") is False:
        fallback = MSG["not_plant"] if status in ("not_a_plant", "no_crop_visible") else MSG["unclear"]
        text = _clean(quality.get("retake_advice")) or fallback
        return Reply("retake", text, [f"bad_photo:{status}"], data)

    primary = (data.get("diagnosis") or {}).get("primary")
    if not isinstance(primary, dict):
        return Reply("unsure", MSG["unsure"], ["no_diagnosis"], data)

    name = str(primary.get("name") or "unknown").strip().lower()
    category = str(primary.get("category") or "unknown").strip().lower()
    confidence = str(primary.get("confidence") or "low").strip().lower()

    if name == "unknown" or category == "unknown" or confidence == "low":
        return Reply("unsure", f"{MSG['unsure']} {MSG['closing']}", ["low_confidence"], data)

    if name == "healthy" or category == "healthy":
        return Reply("healthy", MSG["healthy"], ["healthy"], data)

    flags = []
    message = _clean(data.get("farmer_message"))
    if message is None:
        flags.append("message_unusable_or_dose_found")
    elif len(message.split()) > MAX_WORDS:
        flags.append("message_too_long")
        message = None
    if message is None:
        message = _build_fallback(data, name, confidence)

    if confidence == "medium":
        message = f"{MSG['medium_prefix']} {message}"
        flags.append("medium_confidence")

    ext = data.get("see_extension_officer") or {}
    if ext.get("needed") and "extension officer" not in message.lower():
        message = f"{message} {MSG['extension']}"

    message = f"{message} {MSG['closing']}"
    return Reply("diagnosis", message, flags, data)
```

Branching summary: bad photo gets a retake request; low or unknown confidence says "not sure" and refers to an extension officer; healthy gets no chemicals; a real diagnosis uses the model's message only if it has no dose and is not too long.

## 8. Crop detection case flow (voice or text description)

Starting a case: set `flow = crop_detection`, `current_case_id = new uuid`, `case_started_at = now`, counters to 0.

Each turn while the case is open:

1. Update `last_activity_at`. Tag the stored message with `case_id`.
2. Load this case's messages (`messages` where `case_id` matches) in English, plus the case fields, plus the user's `conversation_memory.summary` if it exists.
3. Call the text model with the text system prompt below. It returns one action.
4. Merge `extracted` facts into `case_crop`, `case_part`, `case_symptoms`.
5. Act on the result:

| `action` | Behaviour |
|---|---|
| `ask_question` | Only if `questions_asked < 2`; otherwise force `diagnose`. Send the question as a voice note, set the matching `step` (`awaiting_crop` or `awaiting_description`), increment `questions_asked` |
| `diagnose` | Only if `diagnoses_in_case < 2`; otherwise reply that you have told them what you can and ask for a photo or an extension officer visit. Build the reply with the text-diagnosis checks (below), send it, set `step = awaiting_photo_optional`, increment `diagnoses_in_case`, write a `diagnosis_log` row with `source = description` |
| `refer` | Description too vague after the questions are used up. Send the `unsure` message. Keep the case open for a photo |
| `off_topic` | Read `switch_to` (`market_price`, `connect_vendor`, `menu`). Close the current case and start that flow immediately |

6. While `step == awaiting_photo_optional`, a new voice or text message adds detail and goes back to step 1 (subject to the diagnosis cap). A photo runs photo diagnosis and closes the case. Silence lets the case expire.

Description-based diagnoses go through the same dose guard as photo diagnoses. In addition: confidence is capped at `medium` (if the model returns `high`, downgrade to `medium`), and the code, not the model, appends the fixed line "To be more sure, please send me a photo of the leaf." Never recommend chemicals from a description alone, so strip any chemical item if the model includes one, or drop to the cultural steps.

### Closing a case

Triggered by: photo diagnosis sent, expiry, or a topic switch.

1. Clear `flow`, `step`, `current_case_id`, `case_*`, counters.
2. If the case produced at least one diagnosis, update `conversation_memory` (section 10). Run it as a background task so it does not delay replies.
3. Old messages stay in the database; they are simply no longer the current case.

### Text system prompt

```python
TEXT_SYSTEM_PROMPT = """
You are a crop health assistant for smallholder farmers in Nigeria. You talk
with a farmer who describes a crop problem. Everything you receive is already
translated into English. You return ONE JSON object and nothing else.

You are given: facts collected so far, the conversation for this problem, how
many questions you have already asked, and optionally a short summary of what
the farmer shared in the past.

RULES
1. Choose one action:
   - "ask_question": the crop or the main symptom is missing. Ask ONE short,
     simple question. Never ask more than one question at a time. If
     questions_asked is already 2 or more, do not ask.
   - "diagnose": you have enough to give a cautious best guess.
   - "refer": the description is too vague to guess from, even after
     questions. Do not invent a diagnosis.
   - "off_topic": the farmer is asking about something else. Set switch_to to
     "market_price", "connect_vendor" or "menu".
2. This is a description, not a photo. Confidence must be "low" or "medium",
   never "high".
3. Never guess when symptoms fit many causes. Lower the confidence and list
   alternatives.
4. Recommend low-cost and cultural steps only (remove infected plants, clean
   cuttings, spacing, sanitation, crop rotation). Do not name any chemical,
   dose, or spray schedule.
5. Use the past summary only for context, for example the farmer's crops and
   location. Never treat a past problem as the current diagnosis.
6. farmer_message: simple English, short sentences, under 60 words, no
   jargon, natural when read aloud. Do not mention photos; the system adds
   that.
7. extracted: only facts the farmer actually said. Use null if unknown.
8. Output ONLY the JSON object below. No markdown, no code fences.

JSON SCHEMA
{
  "action": "ask_question | diagnose | refer | off_topic",
  "switch_to": "market_price | connect_vendor | menu | null",
  "question": "the one question, or null",
  "extracted": {
    "crop": "crop name or null",
    "part": "leaf | stem | fruit | root | whole plant | null",
    "symptoms": ["what the farmer described, one item each"]
  },
  "diagnosis": {
    "primary": {
      "name": "disease or pest name, or 'unknown'",
      "category": "fungal | bacterial | viral | pest | nutrient | abiotic | unknown",
      "confidence": "low | medium"
    },
    "alternatives": [ { "name": "other possible cause", "category": "same options" } ]
  },
  "recommended_actions": ["short cultural steps"],
  "see_extension_officer": { "needed": true or false, "reason": "one sentence" },
  "farmer_message": "short message for the farmer, or null if not diagnosing"
}
"""
```

Set `diagnosis`, `recommended_actions` and `farmer_message` to null unless `action` is `diagnose`.

Which provider and model serve the text prompt is an open question (section 14). Groq is already used for translation.

## 9. Other services (stubs only)

For `market_price` and `connect_vendor`: set `flow`, send a short fixed voice note saying the service is coming soon and the menu is available, clear the flow. Do not build real lookups. Structure the router so each flow has its own handler function, so they can be filled in later.

## 10. Long-term memory

Updated once per closed case that produced a diagnosis, never per message.

Input: the old summary plus the closed case's messages and diagnosis. Output: the new summary, 150 words or fewer, covering crops, location (town or LGA), past problems and advice given, open follow-ups. Rules for the summarizer prompt: include only things the farmer shared; do not add inferences; do not store names, phone numbers or other identifiers beyond what is needed; preserve still-relevant older facts and drop stale ones.

Load the summary into the text model prompt (section 8). Do not send it to the vision model.

## 11. Tests required

Pure unit tests, no network:

- `parse_model_output`: valid JSON, code-fenced JSON, JSON with extra text, garbage, empty.
- `build_reply`: each branch (parse error, not a plant, unclear, no diagnosis block, low confidence, unknown, healthy, medium confidence prefix, dose in farmer_message triggers fallback, message too long, extension officer line appended, closing line appended).
- Dose regex: examples it must catch ("20 ml per litre", "every 7 days", "2 sachets") and ones it must not.
- Expiry: open case within and beyond 3 hours, no case open.
- Router: list reply switches flow mid-case; photo with no case starts one; voice with no flow and clear intent; unclear intent sends menu; duplicate message id ignored.
- Case caps: third question forced to diagnose; third diagnosis blocked; confidence `high` from the text model downgraded to `medium`.

## 12. Deferred and out of scope

- "Please wait, I am looking at it" voice note: add a function `send_wait_notice(user)` that currently does nothing, and call it before the slow steps (STT, model calls). The owner will supply pregenerated audio per language later.
- Real market price and vendor lookups.
- Durable photo storage (see open questions).
- Any change to onboarding.

## 13. Build order

1. Phase 0: read the code, post your plan and questions, wait for answers.
2. Phase 1: schema (`messages`, `diagnosis_log`, `conversation_memory`, user state fields), schemas file, migrations.
3. Phase 2: persist all incoming and outgoing messages, with deduplication.
4. Phase 3: router and `flow`/`step` handling, expiry, menu switching, stubs.
5. Phase 4: photo diagnosis refactor, new vision prompt, `build_reply`, `diagnosis_log`.
6. Phase 5: text case flow (text model call, extraction, questions, description diagnosis, re-diagnosis caps).
7. Phase 6: case closing and `conversation_memory` summarizer.
8. Phase 7: tests, then a manual walkthrough of the example below.

## 14. Open questions to ask the owner

1. Which provider and model should serve the text conversation (Groq is assumed; what model name)? Is there a config entry for it yet?
2. Photo storage: WhatsApp media ids expire, so a durable copy needs storage (S3, Supabase, local disk, other). Until answered, store the WhatsApp media id in `photo_ref` and note in your plan that it is temporary.
3. Which migration tool, if any, does the project use?
4. Should user state fields live on the existing user model or in a separate table?
5. What does the webhook currently return and how fast? Heavy work (STT, models, TTS) must not block the webhook response; confirm how background processing is handled today.
6. How should the welcome-back voice note be worded, and is it generated or pregenerated?

## 15. Example, end to end

```
Farmer (voice): "My plant leaves are turning yellow."
  -> case starts, action ask_question
Bot (voice):    "Which crop is it?"                         step = awaiting_crop
Farmer (voice): "Cassava."
  -> action diagnose (medium confidence), cultural steps only
Bot (voice):    "From what you describe, it may be cassava mosaic disease.
                 Remove the sick plants and use clean cuttings next time.
                 To be more sure, please send me a photo of the leaf."
                                                            step = awaiting_photo_optional
Farmer (photo)
  -> photo diagnosis with case context, build_reply, diagnosis_log row
Bot (voice):    Diagnosis message ending with "If you have another problem, send me another photo."
  -> case closed, conversation_memory updated in the background
```

## 16. Definition of done

- A returning user can send a photo, a voice note or a menu tap and always gets a voice reply in their language.
- No reply ever contains a dose, mixing amount or spray schedule.
- No case can stay open or stuck beyond 3 hours of silence.
- Messages and diagnoses are stored; declined-consent users have nothing stored beyond what onboarding already keeps.
- Schemas file matches the models.
- Tests in section 11 pass.
