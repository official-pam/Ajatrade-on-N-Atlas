LANGUAGES = {
    "lang_ha": "hausa",
    "lang_yo": "yoruba",
    "lang_ig": "igbo",
    "lang_pcm": "nigerian pidgin",
    "lang_en": "english",
}
DEFAULT_LANGUAGE = "english"
MAX_BUTTON_TITLE = 20




def language_question():
    """
    The first onboarding question.

    Since we don't know the user's language yet, the language
    options are presented in their respective languages.
    """

    return {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {
                "text": (
                    "Choose your language / Zaɓi yarenka / "
                    "Yan ede rẹ / Họrọ asụsụ gị"
                )
            },
            "action": {
                "button": "Choose language",
                "sections": [
                    {
                        "title": "Languages",
                        # Same rows as the welcome message (LANGUAGE_OPTIONS
                        # was never defined, so this used to crash).
                        "rows": create_language_message(None)["interactive"]["action"]["sections"][0]["rows"],
                    }
                ],
            },
        },
    }


def create_language_message(name: str | None):
    return {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {
                "text": (
                    f"Welcome to AjaTrade{' ' + name if name else ''}! 👋\n\n"
                    "AjaTrade is a WhatsApp-based, voice-first AI assistant for "
                    "Nigerian farmers, informal traders, and small manufacturers.\n\n"
                    "To proceed, please choose your preferred language "
                    "to continue."
                )
            },
            "action": {
                "button": "Choose language",
                "sections": [
                    {
                        "title": "Languages",
                        "rows": [
                            {
                                "id": "lang_ha",
                                "title": "Hausa",
                            },
                            {
                                "id": "lang_yo",
                                "title": "Yoruba",
                            },
                            {
                                "id": "lang_ig",
                                "title": "Igbo",
                            },
                            {
                                "id": "lang_pcm",
                                "title": "Nigerian Pidgin",
                            },
                            {
                                "id": "lang_en",
                                "title": "English",
                            },
                        ],
                    }
                ],
            },
        },
    }


ROLE_MESSAGES = {
    "english": {
        "body": "What best describes you?",
        "farmer": "Farmer",
        "trader": "Trader",
        "manufacturer": "Manufacturer",
    },
    "yoruba": {
        "body": "Èwo ni ó ṣàpèjúwe rẹ jù lọ?",
        "farmer": "Àgbẹ̀",
        "trader": "Oníṣòwò",
        "manufacturer": "Aṣelọpọ",
    },
    "hausa": {
        "body": "Mene ne ya fi kwatanta ku?",
        "farmer": "Manomi",
        "trader": "Ɗan kasuwa",
        "manufacturer": "Mai ƙera kaya",
    },
    "igbo": {
        "body": "Kedu nke kacha kọwaa gị?",
        "farmer": "Onye ọrụ ugbo",
        "trader": "Onye ahịa",
        "manufacturer": "Onye nrụpụta",
    },
    "pidgin": {
        "body": "Which one best describe you?",
        "farmer": "Farmer",
        "trader": "Trader",
        "manufacturer": "Manufacturer",
    },
}

ROLES = {
    "role_farmer": "farmer",
    "role_trader": "trader",
    "role_manufacturer": "manufacturer",
}


def role_message(language: str = DEFAULT_LANGUAGE) -> dict:
    """Role selection: plain text requesting the role."""
    language = (language or "").strip().lower()
    texts = ROLE_MESSAGES.get(language) or ROLE_MESSAGES["english"]
    return {
        "type": "text",
        "text": {"body": texts["body"]},
    }


def create_role_message(language: str = DEFAULT_LANGUAGE) -> dict:
    """Show the role selection. No internal IDs in the UI."""
    language = (language or "").strip().lower()
    texts = ROLE_MESSAGES.get(language) or ROLE_MESSAGES["english"]
    buttons = []
    for role_id, role in ROLES.items():
        title = texts[role]
        assert len(title) <= MAX_BUTTON_TITLE, f"Button title too long: {title!r}"
        buttons.append({
            "type": "reply",
            "reply": {"id": role_id, "title": title},
        })

    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texts["body"]},
            "action": {"buttons": buttons},
        },
    }
 
ROLES = {
    "role_farmer": "farmer",
    "role_trader": "trader",
    "role_manufacturer": "manufacturer",
}


CONSENT_MESSAGES = {
    "english": (
        "Before we continue, we need your consent.\n\n"
        "We may collect your phone number, voice notes, crop photos, "
        "location, and questions. We use this information to provide "
        "farming and trade advice.\n\n"
        "Do you agree?"
    ),

    "hausa": (
        "Kafin mu ci gaba, muna buƙatar izininka.\n\n"
        "Za mu iya tattara lambar wayarka, saƙonnin murya, "
        "hotunan amfanin gona, wurinka, da tambayoyinka. "
        "Muna amfani da waɗannan bayanan don ba ka shawarar "
        "noma da kasuwanci.\n\n"
        "Ka yarda?"
    ),

    "yoruba": (
        "Ṣáájú kí a tó tẹ̀síwájú, a nílò ìfọwọ́sí rẹ.\n\n"
        "A lè gba nọ́mbà fóònù rẹ, àwọn ohun tí o gbé sílẹ̀ "
        "ní ohùn, àwọn fọ́tò irugbin, ibi tí o wà, àti àwọn ìbéèrè rẹ. "
        "A máa lo wọn láti fún ọ ní ìmọ̀ràn nípa iṣẹ́ àgbẹ̀ àti òwò.\n\n"
        "Ṣé o fọwọ́ sí?"
    ),

    "igbo": (
        "Tupu anyị aga n'ihu, anyị chọrọ nkwenye gị.\n\n"
        "Anyị nwere ike ịnakọta nọmba ekwentị gị, ozi olu, "
        "foto ihe ọkụkụ, ebe ị nọ, na ajụjụ gị. "
        "Anyị na-eji ozi ndị a enye ndụmọdụ gbasara ọrụ ugbo na azụmahịa.\n\n"
        "Ị kwenyere?"
    ),

    "pidgin": (
        "Before we continue, we need your consent.\n\n"
        "We fit collect your phone number, voice notes, crop photos, "
        "location, and questions. We go use the information to give "
        "you farming and trade advice.\n\n"
        "You agree?"
    ),
}

CONSENST_BUTTONS = {
    "english": [
        "i agree",
        "read more",
        "don't save my chats"
    ],
    "yoruba": [
        "Mo gba",
        "Kà sí i",
        "Mẹ́ṣọ́ ọ̀rọ̀ mi mọ́"
    ],
    "igbo": [
        "Anabatara m",
        "Gụọ karia",
        "Anekwala okwu m"
    ], 
    "hausa": [
        "Na yarda", 
        "Karanta sauran", 
        "Kada ka adana tattaunawa ta"
    ], 
    "pidgin": [
        "I gree", 
        "Read more", 
        "No save my chat"
    ]
}

GOODBYE_MESSAGES = {
    "english": "No problem. Thank you for your time. You can message us again anytime.",
    "hausa": "Babu matsala. Mun gode da lokacinka. Za ka iya sake turo mana saƙo a kowane lokaci.",
    "yoruba": "Kò sí ìṣòro. A dúpẹ́ fún àkókò rẹ. O lè tún fi ìránṣẹ́ ránṣẹ́ sí wa nígbàkigbà.",
    "igbo": "Ọ dịghị nsogbu. Daalụ maka oge gị. Ị nwere ike ịkpọtụrụ anyị ọzọ mgbe ọ bụla.",
    "pidgin": "No wahala. Thank you for your time. You fit message us again anytime.",
}


def full_privacy_notice(language: str = "english") -> str:
    """Fuller privacy notice for the 'Read more' link."""
    text = (
        "AjaTrade is an AI assistant. We save your chat history so we can "
        "remember your crops and past questions.\n\n"
        "What we collect: your name, phone number, messages, voice notes, "
        "crop photos, and location (if you send it).\n"
        "Why: to give you farming and trade advice.\n"
        "Who processes it: WhatsApp/Meta, the speech-to-text, translation, "
        "text-to-speech, and crop-diagnosis providers. Some may process your "
        "data outside Nigeria.\n"
        "We never sell your information.\n"
        "How long: up to 90 days for chat history, unless you delete it.\n"
        "Your rights: type \"my data\" to see what is saved, or \"delete my "
        "data\" to erase it.\n"
        "Talk to a real person: type \"human\".\n"
    )
    return text


def consent_message(language: str = "english") -> dict:
    """Privacy notice with the three reply buttons.

    Button labels fall back to English when the stored value has no labels in
    ``CONSENST_BUTTONS`` (Hausa's key has trailing spaces; the text flow stores
    Pidgin as "nigerian pidgin" but the key is "pidgin"). Reported to the owner
    rather than renaming keys here (combined-onboarding-spec section 8,
    decision 3). English is the fallback so a user is never dropped
    mid-onboarding.
    """
    short = CONSENT_MESSAGES.get(
        language, CONSENT_MESSAGES["english"]
    )
    labels = CONSENST_BUTTONS.get(language) or CONSENST_BUTTONS["english"]
   
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": short},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": "consent_yes", "title": labels[0]},
                    },
                    {
                        "type": "reply",
                        "reply": {"id": "consent_read_more", "title": labels[1]},
                    },
                    {
                        "type": "reply",
                        "reply": {"id": "consent_no", "title": labels[2]},
                    },
                ]
            },
        },
    }



WELCOME_MESSAGES = {
    "en": "Welcome to AjaTrade! How can we help you today?",
    "ha": "Barka da zuwa AjaTrade! Yaya za mu taimaka maka yau?",
    "yo": "Káàbọ̀ sí AjaTrade! Báwo la ṣe lè ràn ọ́ lọ́wọ́ lónìí?",
    "ig": "Nnọọ na AjaTrade! Kedu ka anyị ga-esi nyere gị aka taa?",
    "pcm": "Welcome to AjaTrade! How we fit help you today?",
}


RESTART_MESSAGES = {
    "en": "Would you like to start over?",
    "ha": "Kana son mu sake farawa?",
    "yo": "Ṣé o fẹ́ bẹ̀rẹ̀ lẹ́ẹ̀kansi?",
    "ig": "Ị chọrọ ịmalite ọzọ?",
    "pcm": "You wan start over?",
}


def restart_message(language: str = DEFAULT_LANGUAGE) -> dict:
    language = (language or "").strip().lower()
    return text_message(
        RESTART_MESSAGES.get(language, RESTART_MESSAGES["en"])
    )


ROLE_COMPLETE_MESSAGE = {
    "en": "You selected a role. What would you like to ask?",
    "ha": "Muna da hari yadda za a iya help you?",
    "yo": "O gbọ́dọ̀ jẹ́ ni o fẹ́ síwájì sí?",
    "ig": "O na-eji ọ bụla?",
    "pcm": "You selected role. Kese na o want to ask?",
}


def greeting_message(language: str, name: str) -> dict:
    """Post-role greeting: plain text with the user's name."""
    language = (language or "en").strip().lower()
    text = WELCOME_MESSAGES.get(language, WELCOME_MESSAGES["en"])
    return text_message(f"Hello {name}, {text}")


# Shown when the database is unreachable. Kept in English because we cannot
# read the user's chosen language without storage.
# REVIEW: a native speaker should verify this wording.
STORAGE_FALLBACK_MESSAGE = (
    "Sorry, we cannot reach our system right now. Please try again in a few "
    "minutes."
)

# Shown when an unexpected error happens after the reply was queued. Kept in
# English because we may not have been able to read the chosen language.
# REVIEW: a native speaker should verify this wording.
GENERIC_ERROR_MESSAGE = (
    "Sorry, something went wrong on our side. Please send your message again."
)


def text_message(text: str) -> dict:
    """Simple WhatsApp text message."""
    return {
        "type": "text",
        "text": {
            "body": text
        },
    }


# The service menu: one WhatsApp list, at most 10 rows (WhatsApp limit).
# Rows 2-8 are the farm-advice modules (app/services/advisor).
# REVIEW: native speakers to check the Hausa, Igbo, Yoruba and Pidgin rows.
SERVICE_MENU_MESSAGES =  {
    "english": {
      "greeting": "Hello {name}, I am AjaTrade. How can I help you today?",
      "greeting_no_name": "Hello, I am AjaTrade. How can I help you today?",
      "button": "See options",
      "section": "Services",
      "rows": [
        ["crop_detection", "Diagnose crop problem", "Send a photo or voice note"],
        ["irrigation", "Watering & water loss", "Litres per day, leaks, sloped farms"],
        ["soil_health", "Check soil health", "Soil test numbers or describe your soil"],
        ["fertilizer", "Fertilizer plan", "How much, when, and the cost"],
        ["water_source", "Find water for farm", "Best spot for a well or pond"],
        ["weed_control", "Weed control", "Send a weed photo or describe it"],
        ["variety_choice", "Choose seed variety", "Seed that suits your farm and season"],
        ["germination", "Seed germination check", "Count the seeds that came up"],
        ["market_price", "Check market price", "Get today's market price"],
        ["connect_vendor", "Find a vendor", "Herbicide or fertilizer vendor"],
      ]
    },
    "hausa": {
      "greeting": "Sannu {name}, ni ne AjaTrade. Yaya zan iya taimaka muku a yau?",
      "greeting_no_name": "Sannu, ni ne AjaTrade. Yaya zan iya taimaka muku a yau?",
      "button": "Duba zabi",
      "section": "Ayyuka",
      "rows": [
        ["crop_detection", "Gano matsalar shuka", "Aika hoto ko saƙon murya"],
        ["irrigation", "Ban ruwa", "Lita a rana, zubewar ruwa, gona mai gangara"],
        ["soil_health", "Duba lafiyar ƙasa", "Sakamakon gwajin ƙasa ko bayanin ƙasa"],
        ["fertilizer", "Shirin taki", "Nawa, yaushe, da kuɗinsa"],
        ["water_source", "Nemo ruwa don gona", "Wurin da ya dace da rijiya ko tafki"],
        ["weed_control", "Maganin ciyawa", "Aika hoton ciyawa ko bayani"],
        ["variety_choice", "Zaɓin irin shuka", "Irin da ya dace da gonarka da lokaci"],
        ["germination", "Duba tsiron iri", "Ƙidaya irin da ya tsiro"],
        ["market_price", "Duba farashin kasuwa", "Samu farashin yau"],
        ["connect_vendor", "Nemo dillali", "Mai magani ko taki"],
      ]
    },
    "igbo": {
      "greeting": "Ndewo {name}, abụ m AjaTrade. Kedu ka m ga-esi nyere gị aka taa?",
      "greeting_no_name": "Ndewo, abụ m AjaTrade. Kedu ka m ga-esi nyere gị aka taa?",
      "button": "Hụ nhọrọ",
      "section": "Ọrụ anyị",
      "rows": [
        ["crop_detection", "Chọpụta nsogbu akụkụ", "Zite foto ma ọ bụ olu"],
        ["irrigation", "Ịgba mmiri n'ugbo", "Lita kwa ụbọchị, mmiri na-efu efu"],
        ["soil_health", "Nyochaa ala", "Nọmba nyocha ala ma ọ bụ kọwaa ala gị"],
        ["fertilizer", "Atụmatụ fatịlaịza", "Ole, mgbe, na ego ọ ga-efu"],
        ["water_source", "Chọta mmiri maka ugbo", "Ebe kacha mma maka olulu mmiri"],
        ["weed_control", "Njikwa ahịhịa", "Zite foto ahịhịa ma ọ bụ kọwaa ya"],
        ["variety_choice", "Họrọ ụdị mkpụrụ", "Mkpụrụ dabara n'ugbo gị"],
        ["germination", "Nyochaa mkpụrụ puru", "Gụọ mkpụrụ ndị puru"],
        ["market_price", "Lelee ọnụ ahịa ahịa", "Nweta ọnụ ahịa taa"],
        ["connect_vendor", "Chọta onye na-ere", "Onye na-ere ọgwụ/nri osisi"],
      ]
    },
    "pidgin": {
      "greeting": "Hello {name}, I be AjaTrade. How I fit help you today?",
      "greeting_no_name": "Hello, I be AjaTrade. How I fit help you today?",
      "button": "Check options",
      "section": "Our Services",
      "rows": [
        ["crop_detection", "Check crop problem", "Send picture or voice note"],
        ["irrigation", "Water your farm", "How many litres, leak, farm wey slope"],
        ["soil_health", "Check your soil", "Soil test numbers or tell me about your soil"],
        ["fertilizer", "Fertilizer plan", "How much, when, and how much e go cost"],
        ["water_source", "Find water for farm", "Best place to dig well or pond"],
        ["weed_control", "Weed control", "Send weed picture or describe am"],
        ["variety_choice", "Choose seed type", "Seed wey fit your farm and season"],
        ["germination", "Check seed germination", "Count seeds wey don germinate"],
        ["market_price", "Check market price", "Get today market price"],
        ["connect_vendor", "Find vendor", "Find herbicide or fertilizer"],
      ]
    },
    "yoruba": {
      "greeting": "Pẹlẹ o {name}, emi ni AjaTrade. Bawo ni mo ṣe le ràn ọ́ lọ́wọ́ lónìí?",
      "greeting_no_name": "Pẹlẹ o, emi ni AjaTrade. Bawo ni mo ṣe le ràn ọ́ lọ́wọ́ lónìí?",
      "button": "Wo àwọn aṣàyàn",
      "section": "Àwọn Iṣẹ́",
      "rows": [
        ["crop_detection", "Yẹ ìṣòro irúgbìn wò", "Fi àwòrán tàbí ohùn ránṣẹ́"],
        ["irrigation", "Bíbomi rin oko", "Iye omi lójúmọ́, jíjò omi, oko gẹ̀rẹ́gẹ̀rẹ́"],
        ["soil_health", "Àyẹ̀wò ilẹ̀", "Èsì àyẹ̀wò ilẹ̀ tàbí ṣàlàyé ilẹ̀ rẹ"],
        ["fertilizer", "Ètò ajílẹ̀", "Mélòó, ìgbà wo, àti iye owó"],
        ["water_source", "Wá omi fún oko", "Ibi tó dára fún kànga tàbí adágún"],
        ["weed_control", "Ìṣàkóso èpò", "Fi àwòrán èpò ránṣẹ́ tàbí ṣàlàyé"],
        ["variety_choice", "Yan irú irúgbìn", "Irúgbìn tó bá oko àti àsìkò mu"],
        ["germination", "Àyẹ̀wò irúgbìn tó hù", "Ka irúgbìn tó hù jáde"],
        ["market_price", "Wo iye ọjà", "Gba iye ọjà t'òní"],
        ["connect_vendor", "Wá olùtà", "Olùtà egbògi tàbí ajílẹ̀"],
      ]
    }
  }

# The trader menu: shown to users whose role is trader or manufacturer, or
# when anyone says "my shop". Rows are handled by app/trade/flow.py.
# REVIEW: native speakers to check the Hausa, Igbo, Yoruba and Pidgin rows.
TRADER_MENU_MESSAGES = {
    "english": {
      "greeting": "Hello {name}, I am AjaTrade, your sales book. What would you like to do?",
      "greeting_no_name": "Hello, I am AjaTrade, your sales book. What would you like to do?",
      "button": "See options",
      "section": "My shop",
      "rows": [
        ["trade_sale", "Record a sale", "Say what you sold and the price"],
        ["trade_add", "Add or restock goods", "Item, how many, cost and selling price"],
        ["trade_stock", "My goods and stock", "What is left and your prices"],
        ["trade_today", "Today's profit", "Sales, profit and discounts today"],
        ["trade_week", "This week's profit", "Sales and profit since Monday"],
        ["trade_export", "Record for a loan", "Get your sales record as a PDF"],
        ["trade_help", "How it works", "What I can do for your shop"],
        ["farm_menu", "Farm services", "Crop problems, soil, fertilizer"],
      ]
    },
    "hausa": {
      "greeting": "Sannu {name}, ni ne AjaTrade, littafin cinikinka. Me kake so ka yi?",
      "greeting_no_name": "Sannu, ni ne AjaTrade, littafin cinikinka. Me kake so ka yi?",
      "button": "Duba zabi",
      "section": "Shagona",
      "rows": [
        ["trade_sale", "Rubuta ciniki", "Faɗi abin da ka sayar da farashi"],
        ["trade_add", "Ƙara kaya", "Kaya, yawa, kuɗin saye da na sayarwa"],
        ["trade_stock", "Kayana da yawansu", "Abin da ya rage da farashinka"],
        ["trade_today", "Ribar yau", "Ciniki, riba da rangwame na yau"],
        ["trade_week", "Ribar wannan mako", "Ciniki da riba tun Litinin"],
        ["trade_export", "Bayani don lamuni", "Samu bayanan cinikinka a PDF"],
        ["trade_help", "Yadda yake aiki", "Abin da zan iya yi wa shagonka"],
        ["farm_menu", "Ayyukan gona", "Matsalar shuka, ƙasa, taki"],
      ]
    },
    "igbo": {
      "greeting": "Ndewo {name}, abụ m AjaTrade, akwụkwọ ahịa gị. Gịnị ka ị chọrọ ime?",
      "greeting_no_name": "Ndewo, abụ m AjaTrade, akwụkwọ ahịa gị. Gịnị ka ị chọrọ ime?",
      "button": "Hụ nhọrọ",
      "section": "Ụlọ ahịa m",
      "rows": [
        ["trade_sale", "Dee ihe m rere", "Kwuo ihe ị rere na ọnụ ahịa"],
        ["trade_add", "Tinye ngwa ahịa", "Ngwa, ole, ego ịzụ na ego ire"],
        ["trade_stock", "Ngwa ahịa m", "Ihe fọdụrụ na ọnụ ahịa gị"],
        ["trade_today", "Uru taa", "Ahịa, uru na mbelata taa"],
        ["trade_week", "Uru izu a", "Ahịa na uru kemgbe Mọnde"],
        ["trade_export", "Ndekọ maka mbinye ego", "Nweta ndekọ ahịa gị dịka PDF"],
        ["trade_help", "Otu o si arụ ọrụ", "Ihe m nwere ike ime maka gị"],
        ["farm_menu", "Ọrụ ugbo", "Nsogbu akụkụ, ala, fatịlaịza"],
      ]
    },
    "pidgin": {
      "greeting": "Hello {name}, I be AjaTrade, your sales book. Wetin you wan do?",
      "greeting_no_name": "Hello, I be AjaTrade, your sales book. Wetin you wan do?",
      "button": "Check options",
      "section": "My shop",
      "rows": [
        ["trade_sale", "Write down sale", "Talk wetin you sell and the price"],
        ["trade_add", "Add new goods", "Item, how many, cost and sell price"],
        ["trade_stock", "My goods", "Wetin remain and your prices"],
        ["trade_today", "Today profit", "Sales, profit and discount today"],
        ["trade_week", "This week profit", "Sales and profit since Monday"],
        ["trade_export", "Record for loan", "Collect your sales record as PDF"],
        ["trade_help", "How e dey work", "Wetin I fit do for your shop"],
        ["farm_menu", "Farm services", "Crop problem, soil, fertilizer"],
      ]
    },
    "yoruba": {
      "greeting": "Pẹlẹ o {name}, emi ni AjaTrade, ìwé ọjà rẹ. Kí ni o fẹ́ ṣe?",
      "greeting_no_name": "Pẹlẹ o, emi ni AjaTrade, ìwé ọjà rẹ. Kí ni o fẹ́ ṣe?",
      "button": "Wo àwọn aṣàyàn",
      "section": "Ṣọ́ọ̀bù mi",
      "rows": [
        ["trade_sale", "Kọ ọjà tí mo tà", "Sọ ohun tí o tà àti iye owó"],
        ["trade_add", "Fi ọjà kún un", "Ọjà, iye, owó rírà àti owó títà"],
        ["trade_stock", "Àwọn ọjà mi", "Ohun tó kù àti iye owó rẹ"],
        ["trade_today", "Èrè t'òní", "Ọjà, èrè àti ẹ̀dínwó t'òní"],
        ["trade_week", "Èrè ọ̀sẹ̀ yìí", "Ọjà àti èrè láti Ọjọ́ Ajé"],
        ["trade_export", "Àkọsílẹ̀ fún owó yíyá", "Gba àkọsílẹ̀ ọjà rẹ ní PDF"],
        ["trade_help", "Bí ó ṣe ń ṣiṣẹ́", "Ohun tí mo lè ṣe fún ṣọ́ọ̀bù rẹ"],
        ["farm_menu", "Iṣẹ́ oko", "Ìṣòro irúgbìn, ilẹ̀, ajílẹ̀"],
      ]
    }
  }

TRADER_ROLES = ("trader", "manufacturer")


def menu_kind_for(role: str | None, kind: str | None = None) -> str:
    """Which menu to show: an explicit kind, else by role."""
    if kind in ("farm", "trade"):
        return kind
    return "trade" if (role or "").lower() in TRADER_ROLES else "farm"


def service_menu(
    language: str = "english",
    name: str | None = None,
    role: str | None = None,
    kind: str | None = None,
) -> dict:
    """The tappable service menu: the farm menu, or the trader menu for
    traders and manufacturers (or when ``kind`` asks for one)."""
    from app.services.languages import normalize_language

    table = TRADER_MENU_MESSAGES if menu_kind_for(role, kind) == "trade" else SERVICE_MENU_MESSAGES
    msgs = table.get(normalize_language(language), table["english"])
    body = (
        msgs["greeting"].format(name=name)
        if name
        else msgs["greeting_no_name"]
    )
    return {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": msgs["button"],
                "sections": [
                    {
                        "title": msgs["section"],
                        "rows": [
                            {"id": rid, "title": title, "description": desc}
                            for rid, title, desc in msgs["rows"]
                        ],                 
                    }
                ],
            },
        },
    }


