# States: new -> awaiting_language -> awaiting_consent -> awaiting_role -> active / declined
#
# new: first contact from this number
# awaiting_language: language list shown, awaiting reply
# awaiting_consent: privacy notice + I agree / Read more / Don't save my chats
# awaiting_role: Farmer / Trader / Manufacturer
# active: fully onboarded, normal pipeline
# declined: consent declined, no chat history or memory saved
#
# Multi-step flows store the per-user state in the User object (PostgreSQL).

import copy
from datetime import datetime, timezone

from app.schemas.memory import User
from app.storage import storage as default_storage
from app.onboarding.messages import (
    LANGUAGES,
    ROLES,
    GOODBYE_MESSAGES,
    WELCOME_MESSAGES,
    create_role_message,
    language_question,
    create_language_message,
    consent_message,
    role_message,
    restart_message,
    text_message,
    full_privacy_notice,
    service_menu,
    greeting_message,
    ROLE_COMPLETE_MESSAGE,
)


# Flows owned by the main pipeline (handle_message) once started.
OPEN_FLOWS = (
    "crop_detection",
    "market_price",
    "connect_vendor",
    "irrigation",
    "soil_health",
    "fertilizer",
    "water_source",
    "weed_control",
    "variety_choice",
    "germination",
    "trade",
)
# Menu rows handled by the farm-advice engine in the main pipeline.
ADVISOR_MENU_IDS = OPEN_FLOWS[3:10]


def _script_text(script_id: str, language: str | None) -> str:
    """Source text of a pre-generated voice script in the user's language.

    Falls back to English when the script has no text for that language
    (same fallback the audio store uses when sending the voice note), so the
    text version always matches the spoken version.
    """
    try:
        from app.voice.audio_store import default_store

        text = default_store.get_script_text(script_id, language or "english")
    except Exception:
        text = None
    if text:
        return text
    from app.voice.scripts import SCRIPTS

    entry = SCRIPTS.get(script_id, {})
    if isinstance(entry, dict):
        return str(entry.get("english", ""))
    return str(entry)


class OnboardingStateMachine:
    def __init__(self, storage=None):
        self.storage = storage or default_storage

    async def get_or_create_user(self, phone: str, name: str | None = None) -> User:
        user = await self.storage.get_user(phone)
        if user is None:
            user = User(phone=phone, name=name)
            await self.storage.save_user(user)
        return user

    async def restart(self, user: User):
        """Reset onboarding back to the beginning."""
        user.state = "new"
        user.language = None
        user.consent_status = "pending"
        user.consent_at = None
        user.consent_version = None
        user.role = None
        user.name = None
        user.preferred_name = None
        await self.storage.save_user(user)

    async def process(self, phone: str, message: dict, name: str | None = None):
        user = await self.get_or_create_user(phone, name)
        answer = self.normalize_input(message)

        # "menu" shows the service menu to an onboarded user and closes any
        # open flow; it must never wipe their language and consent.
        if answer in {"menu", "main menu"} and user.state == "active":
            user.flow = None
            user.step = None
            user.flow_data = {}
            user.current_case_id = None
            await self.storage.save_user(user)
            return service_menu(user.language, name=user.preferred_name or user.name, role=user.role)

        # Global restart command.
        if answer in {"menu", "start over"}:
            await self.restart(user)
            return language_question()

        if user.state == "new":
            return await self.handle_new(user)

        if user.state == "awaiting_language":
            return await self.handle_language(user, answer)

        if user.state == "awaiting_consent":
            return await self.handle_consent(user, answer)

        if user.state == "awaiting_role" :
            return await self.handle_role(user, answer)

        if user.state == "declined":
            return await self.handle_declined(user, answer)

        if user.state == "active" and bool(user.onboarding) == False:
            print("Active user, answer:", answer)
            if str(user.flow) in OPEN_FLOWS:
                # Inside an open flow: the main AjaTrade pipeline
                # (handle_message) owns this message. Return nothing so the
                # webhook hands it over instead of restarting onboarding.
                return None
            return await self.handle_active(user, answer)

        # # Safety fallback: never lock a user out.
        # user.state = "new"
        # await self.storage.save_user(user)
        return None

    async def handle_new(self, user: User):
        # The first message always starts with the language list.
        # The profile name (set by the webhook) is used for the greeting.
        name = user.name  # set by the webhook before calling process()
        if name:
            user.name = name
        user.state = "awaiting_language"
        await self.storage.save_user(user)
        return create_language_message(name=name)

    async def handle_language(self, user: User, answer: str | None):
        language = None
        print("handle_language answer:", answer)

        # Interactive list selection.
        if answer in LANGUAGES:
            language = LANGUAGES[answer]


        if language is None:
            print(f"{answer}, not in Language ")
            return self.invalid_answer(
                language_question(),
                "Please tap one of the language options.",
            )

        user.language = language
        print("language selected:", language)
        user.state = "awaiting_consent"
        await self.storage.save_user(user)
        return consent_message(user.language)

    async def handle_consent(self, user: User, answer: str | None):
        language = user.language or "english"

        if answer == "consent_yes":
            user.consent_status = "granted"
            user.consent_at = datetime.now(timezone.utc)
            user.consent_version = "v1"
            user.state = "awaiting_role"
            await self.storage.save_user(user)
            return create_role_message(language)

        if answer == "consent_no":
            # Declined: keep only what is needed to run the service (phone for
            # dedupe). No chat history or memory is saved.
            user.consent_status = "declined"
            user.state = "awaiting_role"
            await self.storage.save_user(user)
            return [text_message("we would not store any of our chat history"), create_role_message(language)]
        
        if answer == "consent_read_more":
            # Show the fuller notice, then re-show the buttons.
            messages = [
                text_message(full_privacy_notice(language)),
                consent_message(language),
            ]
            return messages

        return self.invalid_answer(
            consent_message(user.language),
            "Please tap one of the options.",
        )

    async def handle_role(self, user: User, answer: str | None):
        print("Role selected:", answer)
        role = None
        language = user.language or "english"

        # Interactive button selection.
        print("handle_role answer:", answer)
        if answer in ROLES:
            role = ROLES[answer]

        if role is None:
            return self.invalid_answer(
                role_message(language),
                "Please tap one of the role options.",
            )

        user.role = role
        user.state = "active"
        await self.storage.save_user(user)

        #  text_message(
        #     "Thanks! The main AjaTrade assistant pipeline will handle "
        #     "your message here.") , 

        return [service_menu(user.language, name=user.preferred_name or user.name, role=user.role)]
    async def handle_declined(self, user: User, answer: str | None):
        language = user.language or "en"

        if (answer or "").lower() == "yes":
            await self.restart(user)
            return language_question()

        return restart_message(language)

    def invalid_answer(self, current_question: dict, instruction: str):
        """Keep the user in their current state and show the question again."""
        question = copy.deepcopy(current_question)
        if question.get("type") == "text":
            question["text"]["body"] = f"{instruction}\n\n{question['text']['body']}"
        elif question.get("type") == "interactive":
            body = question["interactive"].setdefault("body", {})
            body["text"] = f"{instruction}\n\n{body.get('text', '')}"
        return question

    async def handle_active(self, user: User, answer: str | None):
        """Handle a main-menu choice from an active user with no open flow.

        Option 1 (crop_detection) starts the crop-detection flow. Options
        whose feature is not built yet (market_price, connect_vendor) must
        not start any flow and must keep the user's state unchanged, so they
        can pick another option or send a new message. The farm-advice
        options and any other answer are left to the main pipeline (returns
        None, nothing saved): raw text must never become a flow.
        """
        if answer == "crop_detection":
            user.onboarding = True
            user.flow = answer
            await self.storage.save_user(user)
            return text_message(_script_text("diagnosis_prompt", user.language))

        if answer in ("market_price", "connect_vendor"):
            # Feature not built yet: no flow, no state change.
            return text_message(_script_text("feature_coming_soon", user.language))

        return None

    def normalize_input(self, message: dict) -> str | None:
        """Return the meaningful answer from a WhatsApp message: an interactive
        reply ID, an ordinary text body, or a typed number."""
        interactive = message.get("interactive", {})
        if "button_reply" in interactive:
            return interactive["button_reply"].get("id")
        if "list_reply" in interactive:
            return interactive["list_reply"].get("id")

        text = (message.get("text", {}).get("body") or "").strip().lower()
        if text:
            return text
        return None
