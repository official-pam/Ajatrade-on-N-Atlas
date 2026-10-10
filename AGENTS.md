# AGENTS.md

AjaTrade is a WhatsApp-based, voice-first AI assistant for Nigerian farmers,
informal traders and small manufacturers. It gives advice in Hausa, Yoruba,
Igbo and Nigerian Pidgin (plus English), with no app download. It is built
under JENASOP, pre-revenue, at demo stage. The visual crop diagnosis feature is
the main differentiator. The bot is live, so changes must be small and safe.

Users are often low-literacy and on low bandwidth. Favour short, simple,
voice-friendly replies.

## Stack

- Python 3.11+, FastAPI, uvicorn (port 8000), python-dotenv
- Deployed on Railway from GitHub. Pushing to `main` deploys, so do not push to main, I will do that my self.
- WhatsApp Cloud API (Meta), webhook mode
- Pipeline: WhatsApp -> speech-to-text -> intent routing -> answer
  (RAG with Pinecone planned) -> vision (photos) -> response -> text-to-speech
- Translation: Using Groq for the translation
- Text-to-speech: Spitch (Pidgin voice is still being decided)
- Vision: Groq-hosted vision model as an interim provider. The final
  provider is an open decision (see Decisions)
- Check the existing code before assuming anything about how a provider is
  called. Keep the env var names already used in the repo

## Commands

- Run locally: `uvicorn main:app --reload --port 8000`
- Interactive API docs (built into FastAPI): http://localhost:8000/docs
- Lint: TODO (fill in, e.g. `ruff check .`)
- Type check: TODO (fill in, e.g. `mypy .`)
- Tests: TODO (fill in, or "none yet")

## Architecture rules

- **Adapter** (WhatsApp only): verify webhooks, dedupe, parse messages,
  download media, send messages. No model logic here
- **Brain**: STT, translation, diagnosis, intent routing, TTS, memory. Plain
  Python functions with no WhatsApp imports
- **Strings**: all user-facing text lives in one strings module keyed by
  language code. No hardcoded user-facing text in handlers
- Every provider (translation, STT, TTS, vision) sits behind one function with
  a stable signature, so swapping providers never touches channel code
- Never write the same diagnosis, translation or pricing logic twice

## Product rules

Onboarding collects only **language, consent, role** (farmer, trader,
manufacturer). Other profile questions are deferred.

- Farmer is the customer track. Trader and manufacturer are the vendor track
- After language is chosen, every message uses that language
- Greet by WhatsApp profile name when available. Never show internal IDs
  (button IDs, role IDs) to users
- Say clearly that AjaTrade is an AI assistant, and that voice replies are
  computer-generated
- Always offer a way to reach a real person
- Voice reply scripts: short sentences, no markdown, emoji or URLs, numbers and
  money written as spoken words. Always send a short text version too
- Mark any wording a native speaker should verify with a `# REVIEW` comment
- Keep the bot focused on farming and trade. Politely decline unrelated
  requests

## Safety and privacy (non-negotiable)

- Nothing that stores chat history or memory runs until the user has given
  consent. Record consent status and timestamp. If consent is declined, store
  nothing beyond what is needed to run the service
- Do not store raw audio or photos after processing. Keep the transcript and
  diagnosis text only
- Verify the WhatsApp webhook signature on every request. Respond 200 fast and
  do model work in the background. Dedupe by WhatsApp message ID
- Secrets only in environment variables. Never log tokens, API keys or full
  phone numbers (mask them)
- Never commit `.env`, database files, `__pycache__` or `.vscode`
- Crop diagnosis is guidance, not a guarantee. If confidence is low, ask for a
  clearer photo instead of guessing
- Never invent chemical dosages. Point users to the product label, protective
  gear and a local extension officer for serious outbreaks
- Market prices are indicative. State date and source
- Every external API call gets a timeout, one retry and a polite fallback
  message

## Decisions already made (do not revisit without asking)

- Building a proprietary vision model is deferred. Revisit only if real usage
  shows an accuracy gap, a cost problem at volume, an offline need, or a real
  data moat
- When vision is routed, route by crop type up front, not by calling several
  models on the same image
- Log every diagnosis in a form that can later become labeled data, but only
  for users who consented

## Ask me first before

- Adding or changing a dependency, or installing system tools (such as ffmpeg)
- Introducing a database or any new place user data is stored
- Choosing or switching a provider (STT, TTS, translation, vision)
- Changing the pipeline order or the onboarding flow
- Anything that touches webhook verification, secrets, or deployment config
- Dropping or renaming any column or table. Database changes are additive only

## Working style

- Read the existing code before changing it and follow its patterns
- For anything touching more than one file, propose a short plan and wait for my
  OK before writing code
- Keep changes small and focused. Do not rewrite unrelated code
- Type hints everywhere. Small functions. Consistent `async`/`await`
- Fix lint and type errors before finishing
- You cannot test against real WhatsApp. After each change, tell me exactly how
  to test it on a test number and what I should see
- If a requirement is unclear, ask instead of guessing
- Finish with a short summary: what changed, which files, and how to test

## Read before working on...

Detailed specs are in `SPEC.md`. Open only the section the task touches. When
SPEC.md and this file disagree, tell me instead of picking one.

- Providers, models, environment variables: SPEC.md section 1
- Onboarding, welcome text, menus, greetings, routing: section 2
- Database, tables, anything that stores user data: section 3
- Chat history, memory, consent, "my data", compliance, safe advice: section 4
- User-facing wording, system prompts, voice scripts: section 5
- Webhook, media, sending messages, vendor requests: section 6

What to work on next, and what is currently broken or unverified, is in
`TODO.md`.

## Keeping TODO.md current

- When you finish an item, tell me, then move it to the Done section. Do not
  delete it
- Only move an item you have actually completed and checked. If you could not
  test it, leave it where it is and say so
- Never delete, reword or reorder items you did not work on
- If you find new problems or follow-up work, propose them to me. Do not add
  them on your own
- When you change the Status block, update its date

## Not building yet

- Payments of any kind
- Map interface or live vendor-to-buyer matching
- Outbound template messages or proactive follow-ups
- Storing raw voice or photos for model training
- Self-hosting a custom disease detection model
