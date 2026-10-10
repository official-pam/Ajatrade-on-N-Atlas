"""Export real-user validation evidence (NAIC "Real-world validation").

Reads the chat history and the answer log that the bot already keeps for
users who gave consent, and writes:

- validation_interactions.csv  every inbound message (text, voice transcript,
                               photo, location, menu tap) with language
- validation_answers.csv       every answer the bot gave (photo diagnosis and
                               each farm-advice module) with the model used
- validation_summary.md        totals by language, module and day

Phone numbers are replaced with a salted hash, so the files can be shared
with judges. Raw audio and photos were never stored.

Usage (from the project root, with DATABASE_URL set):

    python scripts/export_validation.py --since 2026-10-10 --out validation/
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.storage.models import DiagnosisLogRow, MessageRow, UserRow  # noqa: E402
from app.storage.postgres import to_async_url  # noqa: E402

WAT = timezone(timedelta(hours=1))


def user_ref(phone: str, salt: str) -> str:
    return "u_" + hashlib.sha256(f"{salt}:{phone}".encode()).hexdigest()[:10]


async def export(since: datetime, out: Path, salt: str) -> None:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        sys.exit("DATABASE_URL is not set")
    engine = create_async_engine(to_async_url(dsn))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    out.mkdir(parents=True, exist_ok=True)

    async with session_factory() as session:
        consented = {
            row.wa_id: row.language
            for row in (await session.execute(select(UserRow).where(UserRow.consent_status == "granted"))).scalars()
        }
        messages = (
            await session.execute(
                select(MessageRow).where(MessageRow.created_at >= since).order_by(MessageRow.created_at)
            )
        ).scalars().all()
        answers = (
            await session.execute(
                select(DiagnosisLogRow).where(DiagnosisLogRow.created_at >= since).order_by(DiagnosisLogRow.created_at)
            )
        ).scalars().all()
    await engine.dispose()

    inbound = [m for m in messages if m.direction == "in" and m.wa_id in consented]
    with open(out / "validation_interactions.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_wat", "user", "language", "type", "text_or_transcript"])
        for m in inbound:
            writer.writerow([
                m.created_at.astimezone(WAT).strftime("%Y-%m-%d %H:%M"),
                user_ref(m.wa_id, salt),
                m.language or consented.get(m.wa_id) or "",
                m.msg_type,
                (m.text or "")[:500],
            ])

    kept = [a for a in answers if a.wa_id in consented]
    with open(out / "validation_answers.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_wat", "user", "language", "service", "crop", "model", "wording", "answer_english"])
        for a in kept:
            writer.writerow([
                a.created_at.astimezone(WAT).strftime("%Y-%m-%d %H:%M"),
                user_ref(a.wa_id, salt),
                consented.get(a.wa_id) or "",
                a.source,
                a.crop or "",
                a.model_name or "",
                ",".join(a.flags or []),
                (a.reply_text_en or "")[:600],
            ])

    users = {m.wa_id for m in inbound} | {a.wa_id for a in kept}
    by_language = Counter((consented.get(u) or "unknown") for u in users)
    by_type = Counter(m.msg_type for m in inbound)
    by_service = Counter(a.source for a in kept)
    by_day = Counter(m.created_at.astimezone(WAT).strftime("%Y-%m-%d") for m in inbound)
    natlas_worded = sum(1 for a in kept if "natlas_summary" in (a.flags or []))
    advisor = sum(1 for a in kept if a.source.startswith("advisor:"))

    lines = [
        "# AjaTrade real-user validation summary",
        "",
        f"Period: {since.astimezone(WAT):%d %b %Y} to {datetime.now(WAT):%d %b %Y %H:%M} WAT",
        "",
        f"- Inbound user interactions (messages, voice notes, photos, taps): **{len(inbound)}**",
        f"- Voice notes transcribed with N-ATLAS ASR: **{by_type.get('voice', 0)}**",
        f"- Answers given: **{len(kept)}** (farm-advice modules: {advisor}, photo diagnoses: {by_service.get('photo', 0)})",
        f"- Farm-advice answers worded by N-ATLaS (passed the number check): **{natlas_worded}** of {advisor}",
        f"- Distinct consented users: **{len(users)}**",
        "",
        "## Users by language",
        *[f"- {lang}: {n}" for lang, n in by_language.most_common()],
        "",
        "## Interactions by type",
        *[f"- {t}: {n}" for t, n in by_type.most_common()],
        "",
        "## Answers by service",
        *[f"- {s}: {n}" for s, n in by_service.most_common()],
        "",
        "## Interactions by day",
        *[f"- {d}: {n}" for d, n in sorted(by_day.items())],
        "",
        "Only users who accepted the privacy notice are included. Phone numbers are replaced by salted hashes.",
    ]
    (out / "validation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {out}/validation_interactions.csv, validation_answers.csv, validation_summary.md")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default=(datetime.now(WAT) - timedelta(days=7)).strftime("%Y-%m-%d"),
                        help="start date YYYY-MM-DD (WAT), default 7 days ago")
    parser.add_argument("--out", default="validation", help="output folder")
    args = parser.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=WAT)
    salt = os.getenv("EXPORT_SALT", "ajatrade-naic-2026")
    asyncio.run(export(since, Path(args.out), salt))


if __name__ == "__main__":
    main()
