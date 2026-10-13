# AjaTrade

AjaTrade is a multilingual, voice-first AI assistant on WhatsApp that helps Nigerian farmers, traders and small manufacturers run better businesses. Farmers speak or type in Yoruba, Hausa, Igbo, Pidgin or English and get advice back as a voice note in their language.

Language understanding, translation and speech recognition run on **N-ATLAS**, Nigeria's open multilingual model (NCAIR / Awarri).

## What it does

- Diagnose crop problems from a photo, voice note or description
- Watering plans and water-loss checks, including sloped farms
- Soil health from a soil-test photo, typed numbers or a description
- Fertilizer plans: bags, timing and cost
- Finding water: best spot for a well or pond
- Weed control, including Striga
- Choosing a seed variety that fits the season and stresses
- Seed germination checks

## Layout

| Path | What it is |
|---|---|
| `app/api`, `app/services/whatsapp` | WhatsApp webhook and sending |
| `app/onboarding`, `app/voice` | onboarding (language, consent, role) and pre-recorded voice notes |
| `app/services/brain.py` | routing and the crop-problem conversation |
| `app/services/advisor/` | the farm-advice conversations |
| `app/agri/` | agronomy calculators and data (pure Python, tested) |
| `app/services/natlas.py` | the only place the bot calls N-ATLAS |
| `natlas_server/` | GPU server that hosts N-ATLaS and the N-ATLAS ASR models |
| `scripts/export_validation.py` | exports real-user evidence |
| `docs/NATLAS_INTEGRATION.md` | technical documentation |

## Run

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload --port 8000
pytest
```

Environment variables are listed in `SPEC.md` section 1. The N-ATLAS server is set up as described in `natlas_server/README.md`.

Powered by Awarri (N-ATLaS).

## How to use
- Send a voice note to our whatsapp number, in your local dialect +234 902 661 1474. (it would automatically detect if it falls under the langugagea we support)
- Go through the onboarding (in order to understand how our bot can be of benefit to you).
- Register as either a trader of manufacuter.
- Ask our bot any questions about your farmland via voice not and get a response back in your choosen dialect.
