# SPEC.md

Detailed specification for AjaTrade. AGENTS.md holds the working rules. This
file holds the detail. Open only the section the task touches.

Sections:

1. Models, providers and environment variables
2. Onboarding, greetings and routing
3. Data model
4. Chat history, memory, privacy and compliance
5. Tone, politeness and voice notes
6. WhatsApp adapter rules and vendor connection

If this file and AGENTS.md disagree, tell me instead of picking one.

---

## 1. Models, providers and environment variables

Read this before swapping a provider, adding an API call, or adding an
environment variable. Switching providers is an ask-first item.

### Providers

| Task | Current | Notes and options later |
|---|---|---|
| Speech-to-text | N-ATLAS ASR (NCAIR1 Yoruba / Hausa / Igbo / Nigerian-accented English), picked by the user's language; Pidgin uses the Nigerian English model | ElevenLabs Scribe kept as an opt-in emergency fallback (`STT_FALLBACK=elevenlabs`, off by default) |
| Text-to-speech | Spitch | SoroTTS for Pidgin only, if Spitch does not support Pidgin (verify first). Nigerian Pidgin voice still being decided. N-ATLAS has no TTS |
| Translation | N-ATLaS (NCAIR1/N-ATLaS, Llama-3 8B fine-tune) | was Groq |
| Crop disease diagnosis | Vision model observes (Groq-hosted, interim), N-ATLaS diagnoses and writes the advice | Final vision provider is an open decision (see below) |
| Intent routing / memory summaries / farm-advice wording | N-ATLaS | was Groq |
| Farm-advice numbers (water, fertilizer, soil, varieties, water sources, germination) | Python calculators in `app/agri`, from FAO-56, FMARD, Open-Meteo | the model never invents these numbers |
| Retrieval | Pinecone (planned, RAG) | not built yet |

Notes:

- Do not present the interim vision model as a solved dependency. It is a
  stopgap
- If vision providers are combined, route by crop type up front (specialist for
  priority crops, generalist otherwise). Do not call several models on the same
  image in sequence
- Confirm the vision model in use accepts image input. Keep whatever is already
  working
- Any model swap must sit behind the same function signature so channel code
  never changes
- Every external API call gets a timeout, one retry and a polite fallback
  message
- Building a proprietary vision model is deferred. Revisit only if real usage
  shows an accuracy gap, a per-call cost problem at volume, an offline
  requirement, or a real proprietary data moat
- Do not add major new libraries without a strong reason. Ask first

### Environment variables

Keep the names already used in the repo. Add only what is missing. Never commit
values.

```
# WhatsApp (Meta Cloud API)
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=

# Models
NATLAS_BASE_URL=            # N-ATLAS server (natlas_server/), e.g. https://<space>.hf.space
NATLAS_API_KEY=             # same value as the server's NATLAS_SERVER_KEY
NATLAS_ASR_URL=             # optional, only if ASR runs on another host
NATLAS_TIMEOUT_SECONDS=90   # optional
SPITCH_API_KEY=             # text-to-speech
GROQ_API_KEY=               # vision (photo observation) only
VISION_MODEL_NAME=          # optional, default qwen/qwen3.8-27b
ELEVENLABS_API_KEY=         # only if STT_FALLBACK=elevenlabs
STT_FALLBACK=               # empty (default) or elevenlabs


# Database
DATABASE_URL=               # PostgreSQL connection string (local dev and Railway)

# Memory and privacy
RETENTION_DAYS=90
PRIVACY_NOTICE_VERSION=1
ADMIN_WHATSAPP_ID=           # where "talk to a real person" requests are sent
```

---

## 2. Onboarding, greetings and routing

Read this before changing the first-contact flow, menus, greetings or routing.
Onboarding collects only **language, consent, role**. Other profile questions
are deferred.

### Step 0: Capture the name

On the first webhook from a new number, read `contacts[].profile.name`:

- Trim it, keep the first word or two, cap the length, strip odd symbols
- If it is missing or unusable, greet without a name
- Save it as the user's `name`. Refresh it if it changes
- If the user says "call me X", save `preferred_name`. It overrides `name`

### Step 1: Warm welcome and language selection

The first message must say who AjaTrade is, that it is an AI assistant, what it
can do, and ask for a language. Keep it short.

> Welcome to AjaTrade, {name}. I am an AI assistant for farmers and traders in
> Nigeria. I can help you check crop problems, find market prices, and connect
> with suppliers. You can send text, voice notes or photos.
> Please choose your preferred language to continue.
> [Choose language]

Language list: Hausa, Yoruba, Igbo, Pidgin, English. Because the language is
not known yet, show this first message in simple English with the language
list directly under it. After the choice, everything is in that language.
Language codes: ha, yo, ig, pcm, en.

### Step 2: Privacy notice and consent

Short, plain language, in the chosen language:

> {name}, before we continue: I save our chats so I can remember your crops and
> past questions. Your messages are processed by trusted partners to give you
> answers. I never sell your information. You can see or delete your data any
> time by typing "my data".
> [I agree] [Read more] [Don't save my chats]

- **I agree:** `consent_status = granted`. Start saving history and memory
- **Read more:** send the fuller notice (see section 4 of this file), then
  show the buttons again
- **Don't save my chats:** `consent_status = declined`. The bot still works but
  saves no chat history or memory. Keep only what is needed to run the service
  (for example message IDs for dedupe, deleted quickly)

### Step 3: Role selection

Three reply buttons: Farmer, Trader, Manufacturer. Save `role` and `segment`.
Ask in the chosen language. Never show button IDs.

Roles: Farmer maps to the customer track. Trader and Manufacturer map to the
vendor track.

### Step 4: Greeting and service menu

Greet by name and show the menu as a list message.

**Farmer:**
> Hello {name}, I am AjaTrade. How can I help you today?
> - Diagnose a crop problem (send a photo or voice note)
> - Check today's market price
> - Connect me to a herbicide/fertilizer vendor
> - Talk to a real person

**Trader / Manufacturer:**
> Hello {name}, I am AjaTrade. Here is how I can help your business grow:
> - Register as a vendor (early access waitlist)
> - List your products or prices
> - Get connected to buyers
> - Learn more about AjaTrade for sellers

**Returning users skip onboarding.** Greet by time of day in Nigerian time
(Africa/Lagos) and, if memory exists, refer to it gently:

> Good morning, {name}. Welcome back. Last time we talked about your maize
> leaves. How are they now?

Free-typed questions and voice notes at any time go to the intent router.

### Step 5: Routing

- Voice note: STT, translate to English if needed, intent routing, answer,
  translate back, TTS, then a voice reply plus a short text version
- Photo: diagnosis, treatment written in the user's language, voice reply
- Price intent: existing price lookup (not working yet, see TODO.md)
- Vendor connection: capture location and input needed, log it, confirm
  (manual referral for MVP). See section 6 of this file
- Vendor track: data-collection funnel, not a live marketplace

### Interactive message limits (WhatsApp)

- Reply buttons: max 3, titles max 20 characters
- List messages: max 10 rows, titles max 24 characters
- Button and row IDs are internal only and never shown to users

### Multi-step flows

Multi-step flows use a per-user `state`, cleared by "menu".

---

## 3. Data model

Read this before adding persistence, tables, columns or anything that stores
user data.

### Current state

There is no database yet. Onboarding state and user data live in an in-memory (the user data has not been created check the model.py file)
dictionary, which is lost on every restart or redeploy. The schema below is the
**target design**. Do not choose a database or create tables without asking me
first.

Notes for the storage decision (PostgreSQL database):


- A managed PostgreSQL database is the alternative, and pgvector can be added
  later for scale
- Changes are additive only: new tables or columns, never drop or rename
- Database files and credentials are never committed

### `users`

| field | notes |
|---|---|
| `wa_id` | primary key (WhatsApp number) |
| `name` | profile name from the webhook (`contacts[].profile.name`), cleaned |
| `preferred_name` | set if the user asks to be called something else. Overrides `name` |
| `language` | ha, yo, ig, pcm, en |
| `role` | farmer, trader, manufacturer |
| `segment` | derived: farmer is customer, trader/manufacturer is vendor |
| `consent_status` | pending / granted / declined |
| `consent_at` | timestamp |
| `consent_version` | version of the privacy notice the user agreed to |
| `state` | current step of a multi-step flow |
| `created_at`, `last_seen_at` | |
| `last_intent` | for debugging and analytics |

### `messages` (chat history, only written when consent is granted)

| field | notes |
|---|---|
| `id` | primary key |
| `wa_id` | foreign key to users |
| `wa_message_id` | unique. Also used to dedupe webhook retries |
| `direction` | in / out |
| `msg_type` | text / voice / image / location / button |
| `text` | message text, or transcript for voice, or diagnosis summary for photos |
| `language` | |
| `intent` | |
| `created_at` | |

### `conversation_memory`

| field | notes |
|---|---|
| `wa_id` | primary key |
| `summary` | short rolling summary: crops, location (town/LGA), past problems and advice, open follow-ups. Only things the user shared |
| `updated_at` | |

### `vendor_requests` (farmer asking for a herbicide/fertilizer/seed vendor)

`request_id`, `wa_id`, `input_needed` (herbicide / fertilizer / seeds / other),
`location_text`, `status` (pending / referred / fulfilled), `created_at`.

### Diagnosis records

Each diagnosis is worth keeping as a structured record (crop, predicted
disease, confidence, provider used, language, timestamp), because accumulated
labeled data could later support a proprietary model. Only for users who
consented. Store diagnosis text and fields, never the photo.

### Media rule

Do **not** store raw audio or photos after processing. Keep the transcript and
the diagnosis text only. Storing media to train or improve models needs
separate explicit consent and is out of scope for now.

---

## 4. Chat history, memory, privacy and compliance

Read this before touching anything that saves, reads or deletes user data, or
anything shown to users about privacy.

### Chat history and memory

Only when `consent_status = granted`:

1. Save every incoming and outgoing message to `messages`
2. When answering, build the model context from: persona/system prompt +
   `conversation_memory.summary` + the last 10 messages + the new message
3. After every N messages (start with 10) or at the end of a session, ask the
   Groq text model to update `summary`. Keep it short and factual. Never
   invent details
4. Use memory naturally. Do not recite the user's whole history back to them

If consent is declined, never write to `messages` or `conversation_memory`.

#### Retention (defaults, the owner confirms)

- Delete raw `messages` older than `RETENTION_DAYS` (default 90) with a
  scheduled cleanup job
- Keep `conversation_memory` until the user deletes it or is inactive for
  12 months

#### User controls (plain text commands, also menu items)

- **"my data"**: show what is saved (name, language, role, last topics) and
  offer delete
- **"delete my data"**: ask for confirmation with buttons, then erase
  `messages`, `conversation_memory` and the profile. Confirm when done
- **"stop saving"**: set consent to declined and stop saving from then on
- **"language"**: change language
- **"menu"**: main menu
- **"human"**: talk to a real person

### Compliance and responsible AI

Nigeria has no dedicated AI Act at the time of writing. The legal baseline is
the **Nigeria Data Protection Act (NDPA) 2023**, enforced by the Nigeria Data
Protection Commission (NDPC), together with the **GAID 2025** implementation
directive. Re-check for new AI legislation before each release. This file is
engineering guidance, not legal advice. The owner should confirm obligations
(NDPC registration, appointing a data protection officer, a DPIA) with a
licensed Data Protection Compliance Organisation or lawyer.

#### Transparency

- Say clearly that AjaTrade is an AI assistant in the welcome message and
  whenever the user asks "are you a person?"
- Say that voice replies are computer-generated
- Keep a fuller privacy notice ("Read more") covering: what is collected, why,
  who processes it (WhatsApp/Meta, the speech, translation, text-to-speech and
  vision providers in use), that some of these process data outside Nigeria,
  how long it is kept, and how to delete it or contact the team

#### Consent and lawful basis

- Ask for consent before saving chat history or memory. Record
  `consent_status`, `consent_at`, `consent_version`
- Collect only what is needed (data minimisation) and use it only for helping
  the user (purpose limitation). No selling or sharing of user data
- Never store raw voice or photos by default

#### User rights

- Let users see, correct and delete their data ("my data", "delete my data")
- Offer "Talk to a real person" at all times. The bot must not make decisions
  with legal or similar significant effect on a person based only on
  automated processing

#### Security

- Keep all keys in environment variables. Never log tokens, API keys or full
  phone numbers (mask them)
- Verify the WhatsApp webhook signature on every request
- Keep the database out of git. Restrict access to it and encrypt backups
- Have a simple breach runbook: contain, assess, and notify the NDPC and
  affected users within the legal deadline (72 hours under the NDPA, verify)
- Do not knowingly collect children's data. If a user appears to be a child,
  stop collecting and keep the conversation simple and safe

#### Safe advice

- Crop diagnosis is guidance, not a guarantee. If confidence is low, ask for a
  clearer photo instead of guessing
- Never invent chemical dosages. Tell users to follow the product label, use
  protective gear, and consult a local agricultural extension officer or
  agronomist for serious outbreaks
- Market prices are indicative. Always state the date and source
- No financial, legal or medical advice beyond basic farm guidance

#### Platform policy

- Meta's WhatsApp Business terms restrict general-purpose AI chatbots. Keep
  AjaTrade focused on farming and trade, and politely decline unrelated
  requests. Verify the current policy

---

## 5. Tone, politeness and voice notes

Read this before writing or changing any user-facing text, system prompt or
voice script. Applies to every text reply and every voice note.

### Tone

- Polite, warm and respectful at all times. Patient, never condescending
- Use the user's name (or `preferred_name`) in greetings and at natural points,
  not in every sentence
- Greet by time of day. Say thank you and please where natural. End with a
  friendly offer of more help
- Short sentences. Plain words. No jargon, no slang, no sarcasm
- Use respectful forms appropriate to each language. Mark any wording a native
  speaker should verify with a `# REVIEW` comment
- If something fails, apologize briefly and say what to do next. Never blame
  the user
- If the user is upset or frustrated, acknowledge it first, then help
- Decline unrelated or harmful requests politely and steer back to what
  AjaTrade can help with
- At most one emoji per message. None in voice scripts

### Voice notes

- Write the reply as a spoken script: short sentences, natural pauses, no
  markdown, no emoji, no URLs
- Write numbers and money the way they are spoken (for example "two thousand
  five hundred naira"), not as symbols
- Keep voice replies under about 30 seconds. Put extra detail in the text
  version
- Start the first voice reply of a session with the user's name and a greeting,
  then keep it direct
- Always send a short text version as well, for low-data and accessibility

### One shared system prompt

Put the persona and tone rules in one shared system prompt used by the intent
router and the treatment writer, so behaviour stays consistent. Role-based
tailoring (farmer vs trader/manufacturer) adds to this prompt and does not
replace it.

---

## 6. WhatsApp adapter rules and vendor connection

Read this before changing webhook handling, media handling, message sending, or
the vendor flow.

### Adapter rules

- Handle Meta's GET verification (`hub.challenge`) and verify
  `X-Hub-Signature-256` on every POST
- Respond 200 fast. Do model work in a background task
- Dedupe by message ID so retries never cause a second reply
- Media download is two steps: fetch the media URL by ID, then download with
  the bearer token
- Voice notes are OGG (Opus). Confirm the speech-to-text provider accepts them;
  if not, convert with ffmpeg (ask first)
- WhatsApp shows a playable voice note only for OGG/Opus. Convert the TTS
  output if needed, upload it as media, then send an audio message
- Interactive limits: reply buttons max 3 (titles max 20 characters), list
  messages max 10 rows (titles max 24 characters). Button and row IDs are
  internal only
- Accept a WhatsApp location message or a typed town/LGA
- Free-form replies only work inside 24 hours of the user's last message. Do
  not build outbound template messages yet
- Multi-step flows use the per-user `state`, cleared by "menu"
- The adapter contains no model logic

### Vendor connection (MVP, data-collection funnel)

When a farmer selects "Connect me to a herbicide/fertilizer vendor":

1. Ask for location (WhatsApp location or typed town/LGA)
2. Ask what input they need (herbicide, fertilizer, seeds, other) with buttons
3. Log it as a vendor request (see section 3 of this file)
4. Reply: "Thank you. We have noted your request. A verified supplier in your
   area will contact you within 24 hours."
5. Manual referral by the AjaTrade team

Phase 2 and 3 (map browsing, live vendor-to-buyer matching) are deferred.

### Vendor track (traders and manufacturers)

Data-collection funnel and early-access waitlist, not a live marketplace:
register as a vendor, list products or prices, get connected to buyers, learn
more about AjaTrade for sellers.
