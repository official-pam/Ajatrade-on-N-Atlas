"""Script source text for the voice onboarding flow.

These are the scripts from VOICE_SPEC.md section 7. Step 1 sends them as text:
the cached voice-note pipeline (generate each script once per language, convert
to OGG/Opus and cache the media id) is not built yet, and the user must never be
left in silence.

# REVIEW: a native speaker should verify this wording before launch.
"""

from app.onboarding.messages import GOODBYE_MESSAGES, full_privacy_notice

# Sent after a failed detection attempt, asking for another voice note.
LOW_CONFIDENCE_RETRY = (
    "Sorry, we could not hear you well. Please send another voice note. "
    "Speak slowly and stay in a quiet place."
)

# Sent when the second attempt also fails and the user defaults to Pidgin.
PIDGIN_DEFAULT_NOTICE = (
    "Sorry, we could not understand your language well. We will talk to you "
    "in Pidgin. Let us continue."
)

# Sent when the voice note is confidently a language we do not support.
# English only for now; no audio file is generated yet. Add it to ``SCRIPTS``
# and run the audio generator (python -m voice.generate_assets) when the voice
# note is recorded. Fallback is still text, so the user is never left in
# silence.
# REVIEW: a native speaker should verify this wording before launch.
UNSUPPORTED_LANGUAGE = {
    "english": (
        "Sorry, we only support Hausa, Yoruba, Igbo, Nigerian Pidgin and "
        "English for now. Please send a voice note in one of these languages."
    ),
}


 
WELCOME_CONSENT = {
    "english": (
        "Welcome to AjaTrade. "
        "AjaTrade is a friendly helper on WhatsApp for farmers and traders. "
        "You can send us a voice note about your farm or your business, "
        "and we will answer you in your language. "
        "To serve you well, we would like to save your phone number and our chat. "
        "Below this message there are three buttons. "
        "Press number one if you agree. "
        "Press number two if you do not agree. "
        "Press number three if you want to hear more about how we use your information. "
     
    ),
    "hausa": (
        "Barka da zuwa AjaTrade. "
        "AjaTrade abokin taimako ne a WhatsApp ga manoma da ’yan kasuwa. "
        "Kuna iya aiko mana da saƙon murya game da gonarku ko kasuwancinku, "
        "mu kuma za mu amsa muku da harshenku. "
        "Domin mu yi muku hidima da kyau, muna so mu adana lambar wayarku da maganganunmu. "
        "A ƙasan wannan saƙo akwai maɓallai uku. "
        "Ku danna lamba ta ɗaya idan kun amince. "
        "Ku danna lamba ta biyu idan ba ku amince ba. "
        "Ku danna lamba ta uku idan kuna son ƙarin bayani kan yadda muke amfani da bayananku. "
        
    ),
    "igbo": (
        "Nnọọ na AjaTrade. "
        "AjaTrade bụ enyi na-enyere aka na WhatsApp maka ndị ọrụ ugbo na ndị ahịa. "
        "Ị nwere ike iziga anyị ozi olu gbasara ugbo gị ma ọ bụ azụmahịa gị, "
        "anyị ga-aza gị n’asụsụ gị. "
        "Ka anyị nwee ike ijere gị ozi nke ọma, anyị chọrọ ichekwa nọmba ekwentị gị na mkparịta ụka anyị. "
        "N’okpuru ozi a, enwere bọtịnụ atọ. "
        "Pịa nọmba mbụ ma ị kwenyere. "
        "Pịa nọmba abụọ ma ị kwenyeghị. "
        "Pịa nọmba atọ ma ịchọrọ ịnụ ihe ndị ọzọ banyere otu anyị si eji ozi gị. "

    ),
    "yoruba": (
        "Káàbọ̀ sí AjaTrade. "
        "AjaTrade jẹ́ olùrànlọ́wọ́ ọ̀rẹ́ lórí WhatsApp fún àwọn àgbẹ̀ àti àwọn oníṣòwò. "
        "Ẹ lè fi ohùn ránṣẹ́ sí wa nípa oko yín tàbí iṣẹ́ ìṣòwò yín, "
        "a ó sì dá yín lóhùn ní èdè yín. "
        "Kí á lè ṣe yín dáadáa, a fẹ́ fi nọ́mbà fóònù yín àti ọ̀rọ̀ tí a bá ń sọ pamọ́. "
        "Ní ìsàlẹ̀ ìránṣẹ́ yìí, bọ́tìnì mẹ́ta wà. "
        "Ẹ tẹ nọ́mbà kìíní tí ẹ bá faramọ́ ọ. "
        "Ẹ tẹ nọ́mbà kejì tí ẹ kò bá faramọ́ ọ. "
        "Ẹ tẹ nọ́mbà kẹta tí ẹ bá fẹ́ gbọ́ àlàyé si i lórí bí a ṣe ń lo ìwífún yín. "
 
    ),
    "pidgin": (
        "Welcome to AjaTrade. "
        "AjaTrade na friendly helper wey dey WhatsApp for farmers and traders. "
        "You fit send us voice note about your farm or your business, "
        "and we go answer you for your language. "
        "So that we fit serve you well, we wan save your phone number and our chat. "
        "For under this message, three buttons dey. "
        "Press number one if you agree. "
        "Press number two if you no agree. "
        "Press number three if you wan hear more about how we dey use your information."
    ),
}


ROLE_ASK = {
    "english": "What best describes you? Press 1 for Farmer, press 2 for Trader, or press 3 for Manufacturer.",
    "hausa": "Wanne ne ya fi bayyana ka? Danna ɗaya idan kai manomi ne, danna biyu idan kai ɗan kasuwa ne, ko ka danna uku idan kai mai masana'anta ne.",
    "igbo": "Kedu nke kachasị kọwaa gị? Pịa otu maka onye ọrụ ugbo, pịa abụọ maka onye ahịa, ma ọ bụ pịa atọ maka onye na-emepụta ihe.",
    "pidgin": "Which one describe you pass? Press 1 for Farmer, press 2 for Trader, or press 3 for Manufacturer.",
    "yoruba": "Kí ni ó ṣàpèjúwe rẹ jùlọ? Tẹ ọkan fún àgbẹ̀, tẹ èjì fún oníṣòwò, tàbí tẹ ẹ̀ta fún olùpèsè."
  }

SERVICE_MENU_PROMPT = {
    "english": "Hello, I am AjaTrade. How can I help you today? Press 1 to diagnose a crop problem, press 2 to check today's market prices, press 3 to find a fertilizer or herbicide vendor, or press 4 to speak with a real person.",
    "hausa": "Sannu, ni ne AjaTrade. Yaya zan iya taimaka muku a yau? Danna ɗaya don gano matsalar shuka, danna biyu don duba farashin kasuwa na yau, danna uku don nemo dillalin taki ko maganin ciyawa, ko ka danna hudu don yi magana da ma'aikaci.",
    "igbo": "Ndewo, abụ m AjaTrade. Kedu ka m ga-esi nyere gị aka taa? Pịa otu iji chọpụta nsogbu akụkụ gị, pịa abụọ iji lelee ọnụ ahịa ahịa taa, pịa atọ iji chọta onye na-ere ọgwụ ma ọ bụ nri osisi, ma ọ bụ pịa anọ iji gwa ezigbo mmadụ okwu.",
    "pidgin": "Hello, I be AjaTrade. How I fit help you today? Press 1 to check crop problem, press 2 to check today market price, press 3 to find fertilizer or herbicide vendor, or press 4 to talk to real person.",
    "yoruba": "Pẹlẹ o, emi ni AjaTrade. Bawo ni mo ṣe le ràn ọ́ lọ́wọ́ lónìí? Tẹ ọkan fún pípèṣè ìṣòro irúgbìn wò, tẹ èjì fún wíwò iye ọjà t'òní, tẹ ẹ̀ta fún wíwá olùtà ajílẹ̀ tàbí egbògi, tàbí tẹ ẹ̀rin fún bíbá ènìyàn gidi sọ̀rọ̀."
  }
 
# Menu option 1 (crop diagnosis): sent when the user picks it from the service
# menu (TODO_new_voice_notes.md).
# REVIEW: a native speaker should verify this wording before launch.
DIAGNOSIS_PROMPT = {
    "english": (
        "Please send a photo or voice note of your crop problem, "
        "and we will diagnose it for you."
    ),
    "yoruba": "Jọwọ fi fọto tabi akọsilẹ ohun ranṣẹ ti ìṣòro ọgbìn rẹ, a yoo ṣe àyẹ̀wò fún ọ.",
    "hausa": "Da fatan a aika da hoto ko saƙon murya na matsalar amfanin gona, kuma za mu tantance ta a gare ku.",
    "igbo": "Biko zipụ foto ma ọ bụ ndekọ olu nke nsogbu mkpụrụ osisi gị, anyị ga-enyocha ya maka gị.",
    "pidgin": "Abeg send photo or voice note of your crop problem, we go diagnose am for you.",
}

# Sent once per request when a photo or voice note arrives and processing
# starts, so slow work never leaves the user in silence.
# REVIEW: a native speaker should verify this wording before launch.
PLEASE_WAIT = {
    "english": "Please, wait. The response might take some time.",
    "yoruba": "Jọwọ, duro. Idahun le gba akoko diẹ.",
    "hausa": "Don Allah, jira. Amsa na iya ɗaukar lokaci.",
    "igbo": "Biko, chere. Azịza nwere ike were oge ụfọdụ.",
    "pidgin": "Abeg, wait. The response fit take some time.",
}

# Sent when the user picks a main menu option whose feature is not built yet
# (market_price, connect_vendor). No flow is started; state is unchanged.
# REVIEW: a native speaker should verify this wording before launch.
FEATURE_COMING_SOON = {
    "english": "This feature will be added in the nearest future.",
    "yoruba": "Àfikún yìí yóò fi kun ní ọjọ́ tó sunmọ̀.",
    "hausa": "Za a ƙara wannan fasalin a nan gaba.",
    "igbo": "Atụmatụ ọhụrụ a ga-etinye n'oge na-adịghị anya.",
    "pidgin": "This feature go be added for the nearest future.",
}

# Sent after consent_yes before the role step starts (VOICE_SPEC section 7).
CONSENT_ACCEPTED_ACK = (
    "Thank you. We will save your chat and your phone number."
)

# Prepended when a step is replayed after an answer nobody could map to an
# option (VOICE_SPEC sections 6.3 and 7). English only for now; the language
# packs above are interim English text while cached audio is not built.
STEP_REPLAY_UNCLEAR = (
    "Sorry, we did not understand. "
    "Please press one of the buttons below, or say your choice in a voice note."
)

# How many times a step is replayed after unclear answers before going quiet
# (VOICE_SPEC 6.3: after two failed replays, send nothing more and wait).
MAX_STEP_REPLAYS = 2

# All scripts by id, so the audio generator can loop over them.
SCRIPTS = {
    "welcome_consent": WELCOME_CONSENT,
    "role_ask": ROLE_ASK,
    "service_menu_prompt": SERVICE_MENU_PROMPT,
    "diagnosis_prompt": DIAGNOSIS_PROMPT,
    "please_wait": PLEASE_WAIT,
    "feature_coming_soon": FEATURE_COMING_SOON,
    "consent_ack": {"english": CONSENT_ACCEPTED_ACK},
    "goodbye": GOODBYE_MESSAGES,
    "privacy_notice": {
        lang: full_privacy_notice(lang)
        for lang in ["english", "hausa", "igbo", "yoruba", "pidgin"]
    },
}
 
 
def get__welcoming_script(language: str) -> str:
    """Return the script text for a script id and language.
 
    Falls back to English if the language is missing.
    """
    texts = SCRIPTS["welcome_consent"]
    return texts.get(language, texts["english"])