# TODO

Task list and project status for AjaTrade. Rules for how to work are in
AGENTS.md. Detailed specs are in SPEC.md. This file changes often, so check
the date below before trusting it.

How this file is maintained: I set the priorities. The agent may move a
finished and checked item to Done and update the status date, but must not
delete, reword or reorder other items. New items are proposed to me first.

## Status (updated 2026-10-03)

- Working end to end: webhook replies, language selection, role selection,
  voice notes, photo diagnosis
- Translation has moved from Gemini to Groq (finished)
- Not working: price lookup
- Storage: everything (onboarding state, user data) lives in an in-memory
  dictionary, so it is lost on every restart or redeploy
- Not started: consent step, saved chat history, memory, role-based tailoring
- **Verify the onboarding basics** (see "Verify" below). Fix whatever is still
   broken before adding features.
- **Consent step.** Short privacy notice in the chosen language with
   "I agree", "Read more" and "Don't save my chats". Store consent status and
   timestamp. If declined, save nothing.

## Next up (in this order)

1. **Track conversations.** Save incoming and outgoing messages, only for users
   who consented. Store transcripts and diagnosis text, never raw audio or
   photos. (Go over the SPEC.md file go to section 3 to see the Data model used)
   
2. **Recall previous conversations.** Build the model context from a short
   rolling summary plus the last few messages. Use memory naturally and do not
   recite the user's history back to them.
3. **Tailor responses by role.** Farmer replies and trader/manufacturer replies
   use different menus and different system prompts, in one shared persona and
   tone prompt.
4. **User controls:** "my data", "delete my data", "stop saving", "menu",
   "language", "human".

## Scope (only what the dictionary holds today):
- Read SPEC.md section 3 (Data Model)
- First find every place the code reads or writes the in-memory dictionary
   and list them in the plan. Do not change any code until I approve the plan
- If the code already reads and writes through functions, keep those same
   function names, parameters and return values, and replace only what is
   inside them so they use PostgreSQL. Callers and handlers must not change
- If any code touches the dictionary directly (for example
   `users[wa_id]["language"] = ...`), create small functions that do the same
   job, put them in one storage module, and replace the direct access with
   calls to them. Name them after what the existing code is doing
- Handlers and the WhatsApp adapter never contain SQL
- Keep the ~shape of the data the same: the same fields, and the same
     result when a user does not exist yet
- Read the connection string from `DATABASE_URL`. Never commit it. Add it to
   the env var list in SPEC.md section 1
- Use a migration tool so the same schema can be created locally and on
   Railway. Changes are additive only
- Database calls must not block the webhook. Respond 200 fast and keep model
   work in the background. Use timeouts on every database call
- If the database is unreachable, send the user a polite fallback message
   and log the error. Never crash the webhook
- Do not log full phone numbers. Mask them

Out of scope (later items): messages, conversation_memory, vendor_requests,
the consent flow, retention cleanup.


## Verify (may already be fixed, check before touching)

- Welcome message uses the WhatsApp profile name
- Welcome explains what AjaTrade does and says it is an AI assistant
- After choosing a role, users never see internal IDs such as `role_farmer`
- After choosing a language, every later message uses it (text and voice)
- A greeting and a service menu appear after the role is chosen
- Webhook signature is verified and duplicate messages are ignored
- Logs contain no tokens, API keys or full phone numbers
- `.gitignore` covers `.env`, `__pycache__`, `.vscode` and any database file,
  and none of them are tracked by git

## Later

- Price lookup returning a real answer with date and source
- Vendor connection flow (location, input needed, log the request, manual
  referral by the team)
- Vendor track waitlist for traders and manufacturers
- Retention cleanup job for old messages
- Decide the final crop disease vision provider (see AGENTS.md decisions)
- Decide the Nigerian Pidgin voice for Spitch
- Test end to end with 2 to 3 real numbers across all roles and languages,
  including declined consent and data deletion

## Done

- Webhook replying
- Language selection
- Role selection
- Voice notes
- Photo diagnosis
- Gemini to Groq translation migration
