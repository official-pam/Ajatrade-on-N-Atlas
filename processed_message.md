# Todo: Move webhook message-id dedup from memory to Postgres

## Goal

`mark_seen(message_id)` currently stores WhatsApp message ids in an in-memory set (`_seen_message_ids`). The set is wiped on every restart or redeploy, so Meta retries and replayed backlog look like new messages and get processed again (duplicate voice notes, onboarding replaying). Replace the set with a Postgres table so dedup survives restarts.

Behavior must stay the same everywhere else. This task touches dedup only. Do not change onboarding, translation, TTS, STT, vision or menu logic.

## Hard constraints

- Do not break the webhook. It must still return 200 quickly and do the processing in the background task, as it does now.
- If the database is unreachable or the insert fails, log the error and process the message anyway (return "first time seen"). Never drop a user's message because of a dedup failure.
- An empty or missing `message_id` keeps the current behavior: treated as not seen, processed.
- Keep the app single-worker friendly, but the logic must also be safe if two requests for the same id arrive at the same instant (the database primary key is what guarantees this, not Python code).
- Follow the existing conventions in the codebase for models, sessions, migrations, naming, logging and `mask_phone`. Read how they are done before writing anything new.
- Do not log raw phone numbers. Use `mask_phone` as the rest of the code does.

## Step 0: Read first, change nothing

Before editing, read and understand:

1. `webhook.py`, in particular where `seen = mark_seen(message_id)` is called and whether the handler is `async def`.
2. The module that defines `mark_seen` and `_seen_message_ids`.
3. Every other place that imports or calls `mark_seen` (search the whole repo, including tests).
4. `storage/model.py`, the SQLAlchemy base, and how the async engine and sessions are created and used (session factory, dependency, or context manager).
5. The migrations folder: which tool is used (Alembic or other), how revisions are named, and what the latest revision is.
6. The schema folder and the storage schemas file, so the new schema file matches the existing format.
7. `AGENTS.md`, and follow every rule in it.
8. Where the app starts up and shuts down (lifespan handler or `on_event`), for the cleanup step.
9. How the DB URL is read from `.env`.

If anything below conflicts with what you find in the code, follow the code's conventions and tell me what you changed and why.

## Step 1: Add the model

In `storage/model.py`, add a `ProcessedMessage` model, table name `processed_messages`:

- `message_id`: string, primary key. This is the WhatsApp `wamid...` id.
- `wa_id`: string, nullable. The sender's number in the same format the users table uses, so records correlate.
- `received_at`: timezone-aware datetime, server default `now()`, not null.

Add an index on `received_at` (used by the cleanup query).

## Step 2: Add the migration

Create a new migration with the project's migration tool, following its naming convention and chaining it after the latest revision.

- `upgrade`: create the `processed_messages` table and the `received_at` index.
- `downgrade`: drop the index and the table.

Do not edit any existing migration. Do not autogenerate blindly: check the generated file contains only this table and index, and nothing unrelated.

## Step 3: Rewrite `mark_seen`

Make `mark_seen` async and DB-backed. Signature: `async def mark_seen(message_id: str, wa_id: str | None = None) -> bool`. Returns `True` when this is the first time the id is seen, `False` for a duplicate.

Logic:

1. If `message_id` is empty, return `True`.
2. Open a session the way the rest of the code does.
3. Run an atomic insert that does nothing on conflict, using the PostgreSQL dialect insert with `on_conflict_do_nothing(index_elements=["message_id"])` and `.returning(ProcessedMessage.message_id)`. Commit.
4. If a row is returned, it is new: return `True`. If nothing is returned, it is a duplicate: return `False`.
5. Wrap the database work in try/except. On any exception, roll back if needed, log the error with `logger.exception` (message id only, no raw phone), and return `True` so the message is processed anyway.

Remove `_seen_message_ids` and the in-memory logic completely. Do not keep a second in-memory copy.

## Step 4: Update the call site and any other callers

- In `webhook.py`, change `seen = mark_seen(message_id)` to `seen = await mark_seen(message_id, phone)`, passing the same phone value that onboarding uses (unmasked, for storage only). Confirm the enclosing function is `async def`; if it is not, tell me before changing anything.
- Keep the duplicate branch exactly as it is: log with `mask_phone(phone)` and `return {"status": "received"}`.
- Update every other caller found in Step 0, including tests and mocks, to the new async signature.

## Step 5: Cleanup of old rows

Message ids only need to be remembered for as long as Meta might retry or replay, so delete old rows. Retention: 7 days, defined as a named constant (for example `PROCESSED_MESSAGE_RETENTION_DAYS = 7`).

- Add `async def purge_old_processed_messages()` that runs `DELETE FROM processed_messages WHERE received_at < now() - interval '7 days'`, using the constant, inside try/except that logs and never raises.
- Call it once at app startup, using the project's existing startup mechanism. A failure here must never stop the app from starting.
- Also run it once every 24 hours with a small background asyncio task started at startup and cancelled cleanly on shutdown. Keep it simple, and skip this part and tell me if the app's startup structure makes it risky.

## Step 6: Schema documentation

Per `AGENTS.md`:

- Create the schema file for `processed_messages` in the schema folder, in the same format and naming style as the existing schema files there. It should document the columns, the primary key, the index, and the purpose of the table.
- Update the storage schemas file to reference the new table.

## Step 7: Tests

If the project has tests, add tests for `mark_seen`:

- First call with a new id returns `True`; second call with the same id returns `False`.
- Empty id returns `True` and writes nothing.
- A database failure returns `True` (process anyway) and does not raise.
- Two concurrent calls with the same id: exactly one returns `True`.

If the project has no test setup, do not create one. Tell me instead.

## Step 8: Verify before finishing

1. Run the migration against the local Postgres and confirm the table and index exist.
2. Start the app locally and send the same webhook payload twice. The first is processed; the second logs "Duplicate webhook message ... ignoring".
3. Restart the app and send the same payload again. It must still be ignored (this is the bug being fixed).
4. Stop Postgres, send a new message, and confirm it is still processed and an error is logged.
5. Confirm no references to `_seen_message_ids` remain, and that the app imports and starts cleanly.
6. Confirm the diff touches only: the model, the new migration, the dedup module, `webhook.py`, startup wiring, the new schema file, the storage schemas file, and tests.

## Notes for me (not for the agent to action)

- Dedup is only shared between servers that use the same database. Run either the local server or Railway against the webhook, not both, unless they share one database.
- The Railway database needs the migration applied before the new code is deployed there.

## Report back

When finished, give me: the list of files changed, the migration revision id, anything that did not match this spec and what you did instead, and the results of each check in Step 8.
