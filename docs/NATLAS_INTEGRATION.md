# AjaTrade: technical documentation and N-ATLAS integration

AjaTrade is a voice-first WhatsApp assistant for Nigerian smallholder farmers. A farmer speaks or types in Yoruba, Hausa, Igbo, Pidgin or English and gets farm advice back as a voice note in the same language, plus a short text card with the exact numbers. No app download, no literacy requirement.

Problem statement: **Voice-First Access** (N-ATLAS ASR for every voice note, N-ATLaS for all language work), applied to agriculture.

## What the farmer can do

| Menu option | What it answers | How the answer is produced |
|---|---|---|
| Diagnose crop problem | "What is wrong with my plant?" from a photo, voice note or text | vision model lists what it sees; **N-ATLaS** decides the likely cause and writes the advice; dose filter |
| Watering and water loss | litres per day per zone, how often to water, likely leaks or runoff, sloped fields | FAO-56 water balance with today's ET0 and rain (Open-Meteo); slope zone factors that calibrate from the farmer's feedback |
| Soil health | plain ratings of pH, salt, organic matter, N, P, K, with actions | lab report photo (numbers read by vision) or typed values or description; measured and estimated values kept apart |
| Fertilizer plan | bags, kg, grams per plant, timing, cost in naira | FMARD recommendation tables converted to products; adjusted for soil test, slope, previous legume, rain forecast |
| Find water for the farm | groundwater likelihood, best spot for a well or pond, rain a pond could catch | 25-point elevation grid around the farm, last year's rainfall, farmer's observations; always advises a VES survey before drilling |
| Weed control | timing, cultural and mechanical control, herbicide by active ingredient only | weed photo or description; Striga handled separately |
| Choose seed variety | maturity class that fits the rain left, stress traits to ask for, named varieties with fit and reasons | season-length rules by zone, stress flags, sourced variety list; no invented percentages |
| Seed germination check | germination %, replant advice, likely causes | counts from a tray/row photo or from the farmer |

## Where N-ATLAS is used

```
WhatsApp voice note ──► N-ATLAS ASR (by user language) ──► transcript
WhatsApp text (non-English) ──────────────────────────────► N-ATLaS translate → English
                                                                   │
                                     ┌─────────────────────────────┤
                                     ▼                             ▼
                        N-ATLaS intent routing          N-ATLaS fact extraction
                                     │                    (crop, size, place...)
                                     ▼                             │
                    crop diagnosis: N-ATLaS reasons        app/agri calculators
                    from the vision observations           (numbers, in code)
                                     │                             │
                                     └──────────► N-ATLaS spoken summary ◄─┘
                                                  (number check in code)
                                                         │
                                     N-ATLaS translate → farmer's language
                                                         │
                                              Spitch TTS ──► voice note + text card
```

| Step | File | N-ATLAS component |
|---|---|---|
| Voice-note transcription (all users, all languages) | `app/services/speech_to_text.py` → `app/services/natlas.py:transcribe` | NCAIR1 Yoruba / Hausa / Igbo / Nigerian-English ASR |
| First voice note, language unknown | same, `language=auto` | all four ASR models, most confident wins |
| Language detection during onboarding | `app/services/translation.py:language_detector` | N-ATLaS |
| Translation into English and back | `app/services/translation.py` | N-ATLaS |
| Intent routing | `app/services/brain.py:classify_intent` | N-ATLaS (keyword shortcut first) |
| Crop-problem conversation from description | `app/services/brain.py` | N-ATLaS |
| Photo diagnosis reasoning and advice | `app/services/diagnoses/disease_detection.py` | N-ATLaS |
| Farm facts from free speech | `app/services/advisor/extract.py` | N-ATLaS |
| Spoken summary of each farm-advice answer | `app/services/advisor/compose.py` | N-ATLaS |
| Memory summaries | `app/services/brain.py:summarize_and_store_memory` | N-ATLaS |

Not N-ATLAS (no N-ATLAS model exists for these): text-to-speech (Spitch) and image perception (a vision model that only describes what is in the photo; it never advises).

Every N-ATLAS call goes through one module, `app/services/natlas.py`, which talks to `natlas_server/` over HTTPS with a shared secret, a timeout and one retry.

## Keeping a language model away from wrong numbers

- All quantities (litres, kg, bags, grams per plant, naira, percentages, dates) are computed in `app/agri/` from published sources and unit-tested (`app/agri/test_agri.py`).
- N-ATLaS only phrases them. `compose.check_summary` rejects any summary that contains a number not present in the computed facts, is too long, or (for weeds) mentions a dose; the farmer then gets a plain sentence built in code.
- The text card carries the exact numbers. If translation changes any number in the card, the English card is sent instead.
- Crops without an official fertilizer rate get no numbers, only a referral.
- Pesticide and herbicide doses are never given; products are named by active ingredient and the farmer is sent to the label and agro-dealer.

## Sources

- FAO Irrigation and Drainage Paper 56: crop coefficients and stage lengths (`app/agri/crops.py`).
- FMARD fertilizer recommendations as tabulated in *Review of Fertilizer Use by Crop and by Product, Nigeria 2015–2017* (AfricaFertilizer.org / IFDC, 2018) (`app/agri/fertilizer.py`).
- Soil rating limits commonly used by Nigerian soil labs (Esu 1991; FPDD 1989) and FAO salinity classes (`app/agri/soil.py`).
- Variety facts from IAR/ABU, IITA/CGIAR, NRCRI and NCRI release announcements (`app/agri/varieties.py`).
- Open-Meteo: ET0, rain forecast, rainfall history, 90 m elevation (`app/agri/weather.py`).

Lines marked `# REVIEW` are for a native speaker or agronomist to confirm.

## Real-world validation evidence

The bot already stores, for consenting users only, every inbound message (voice notes as transcripts, never audio) and every answer with the model that produced it. To export the evidence:

```bash
python scripts/export_validation.py --since 2026-10-10 --out validation/
```

This writes `validation_interactions.csv`, `validation_answers.csv` and `validation_summary.md` (totals by language, service and day, voice notes transcribed with N-ATLAS ASR, answers worded by N-ATLaS). Phone numbers are replaced by salted hashes.

## Running it

- Bot: `uvicorn main:app --port 8000` (unchanged). New environment variables: `NATLAS_BASE_URL`, `NATLAS_API_KEY` (see SPEC.md section 1). Run `alembic upgrade head` once to add `users.flow_data` and `users.farm_profile`.
- N-ATLAS server: see `natlas_server/README.md`.
- Tests: `pytest` (calculators, every farm-advice conversation with a fake N-ATLaS, the WhatsApp pipeline, and the existing suite).

## Limits

- The vision step still uses a general vision model; N-ATLaS reasons over its observations. A crop-specific detector is the planned replacement.
- Pidgin has no dedicated N-ATLAS ASR; it uses the Nigerian-accented English model.
- Variety list and soil limits are deliberately short and sourced; they should grow with the NACGRAB register and partner-lab input.
- Slope factors start from 100 / 85 / 65 % and calibrate per farm from feedback; they are not universal values.

N-ATLaS and the N-ATLAS ASR models are by NCAIR / Awarri, used under the N-ATLaS Open-Source Research and Innovation License. Powered by Awarri.
