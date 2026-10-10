"""Voice-note onboarding for AjaTrade.

A separate pipeline from the text onboarding (``app.onboarding``) so the text
flow behaves exactly as it does today. The text flow is the reference: the same
states, the same storage calls and the same stored values, so a user can start
with a voice note and carry on by text (or the reverse).

Only step 1 is built so far: language detection for a first voice note.
"""

# from app.voice.onboarding import VoiceOnboarding, classifier_code_to_stored_language

# __all__ = ["VoiceOnboarding", "classifier_code_to_stored_language"]
