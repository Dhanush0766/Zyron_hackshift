"""Patient voice prompts in the patient's language (MedGate addition, accessibility).

The device plays a pre-recorded clip from its SD card: /voice/<lang>/<KEY>.wav
(MAX98357A I2S amplifier, already in the IDF-B parts list). Recorded human
voice is used instead of text-to-speech because no offline TTS engine on an
ESP32 speaks Manipuri, and elderly users understand a familiar voice better.

TEXT below is what the clip says (also shown on the dashboard and the OLED).
  en  English
  hi  Hindi
  mni Manipuri: TO BE WRITTEN AND RECORDED BY A NATIVE SPEAKER ON THE TEAM.
      Until then the dashboard falls back to English text, marked as a fallback.
      (We did not machine-translate Manipuri: a wrong medical instruction is worse than none.)
"""
from __future__ import annotations

LANGS = {"en": "English", "hi": "Hindi", "mni": "Manipuri"}

PROMPTS = {
    "DUE": {
        "en": "It is time for your medicine. Please tap your card, place your finger on the sensor and measure your blood pressure.",
        "hi": "दवा का समय हो गया है। कृपया अपना कार्ड लगाइए, उंगली सेंसर पर रखिए और ब्लड प्रेशर नापिए।",
    },
    "RETAKE": {
        "en": "The reading was not clear. Please sit still and measure again.",
        "hi": "रीडिंग साफ़ नहीं आई। कृपया आराम से बैठिए और फिर से नापिए।",
    },
    "SCAN_CARD": {
        "en": "Please tap your patient card on the reader.",
        "hi": "कृपया अपना मरीज़ कार्ड रीडर पर लगाइए।",
    },
    "WRONG_PATIENT": {
        "en": "This card does not belong to this medicine box. The medicine will not be given.",
        "hi": "यह कार्ड इस दवा डिब्बे का नहीं है। दवा नहीं दी जाएगी।",
    },
    "CONFIRM_MEAL": {
        "en": "This medicine must be taken with food. If you have eaten, hold your hand over the tray for two seconds.",
        "hi": "यह दवा खाने के साथ लेनी है। अगर आपने खाना खा लिया है, तो दो सेकंड के लिए हाथ ट्रे के ऊपर रखिए।",
    },
    "HELD": {
        "en": "Your readings need a doctor's check. Your doctor and family have been informed. Do not take this dose now.",
        "hi": "आपकी रीडिंग डॉक्टर को दिखानी होगी। डॉक्टर और परिवार को बता दिया गया है। अभी यह दवा मत लीजिए।",
    },
    "TOO_SOON": {
        "en": "You already had this medicine recently. It will not be given again now.",
        "hi": "यह दवा आप अभी कुछ समय पहले ले चुके हैं। अभी दोबारा नहीं दी जाएगी।",
    },
    "DELIVERED": {
        "en": "Your tablet is in the tray. Please take it now.",
        "hi": "आपकी गोली ट्रे में है। कृपया अभी ले लीजिए।",
    },
    "REMINDER": {
        "en": "Reminder: your tablet is still waiting in the tray.",
        "hi": "याद दिलाना: आपकी गोली अभी भी ट्रे में रखी है।",
    },
    "SOS_ACK": {
        "en": "Help is on the way. Your family and doctor have been alerted. Please sit down and stay calm.",
        "hi": "मदद आ रही है। आपके परिवार और डॉक्टर को सूचना दे दी गई है। कृपया बैठ जाइए और शांत रहिए।",
    },
    "EXPIRED": {
        "en": "This medicine has expired and will not be given. Your family has been told to replace it.",
        "hi": "यह दवा एक्सपायर हो गई है, इसलिए नहीं दी जाएगी। परिवार को इसे बदलने के लिए कहा गया है।",
    },
    "COLLECTED": {
        "en": "Thank you. Your dose has been recorded.",
        "hi": "धन्यवाद। आपकी दवा दर्ज कर ली गई है।",
    },
}


def prompt(key: str, lang: str) -> dict:
    texts = PROMPTS[key]
    lang = lang if lang in LANGS else "en"
    text = texts.get(lang)
    return {"key": key, "lang": lang, "clip": f"/voice/{lang}/{key}.wav",
            "text": text or texts["en"], "fallback": text is None}
