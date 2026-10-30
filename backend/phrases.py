"""Fixed sentences the call speaks, per language, plus Twilio language codes.

Ask a native speaker to check the Hindi and Kannada lines before the demo.
"""
import config

LANGS = {
    "en": {"name": "English", "code": "en-IN"},
    "hi": {"name": "Hindi", "code": "hi-IN"},
    "kn": {"name": "Kannada", "code": "kn-IN"},
}

PHRASES = {
    "en": {
        "family": "your family",
        "intro": "Hello! This is CareCall, with a reminder from {sender}.",
        "ask": "Did you do it? Say yes, or say later, or ask me a question. You can also press 1 for done, 2 for later, or 3 if you need help.",
        "reask": "Did you do it? Press 1 for done, 2 for later, or 3 for help.",
        "unclear": "Sorry, I did not catch that. Please press 1 for done, 2 for later, or 3 for help.",
        "done": "Thank you. I will let the sender know. Goodbye!",
        "later": "Okay, I will call you again in {m} minutes. Goodbye!",
        "worrying": "I am sorry to hear that. I will inform the sender right away. Please take care.",
        "noinfo": "I don't have that information, I'll let the sender know.",
        "bye_unclear": "I will try again a little later. Goodbye.",
    },
    "hi": {
        "family": "आपके परिवार",
        "intro": "नमस्ते! यह केयरकॉल है, {sender} की ओर से एक रिमाइंडर।",
        "ask": "क्या आपने यह कर लिया? हाँ बोलिए, बाद में बोलिए, या कोई सवाल पूछिए। हो गया तो 1, बाद में के लिए 2, और मदद चाहिए तो 3 दबाइए।",
        "reask": "क्या आपने यह कर लिया? हो गया तो 1, बाद में के लिए 2, मदद के लिए 3 दबाइए।",
        "unclear": "माफ़ कीजिए, मुझे समझ नहीं आया। हो गया तो 1, बाद में के लिए 2, मदद के लिए 3 दबाइए।",
        "done": "धन्यवाद। भेजने वाले को बता दिया जाएगा। नमस्ते!",
        "later": "ठीक है, {m} मिनट बाद फिर से कॉल आएगा। नमस्ते!",
        "worrying": "यह सुनकर दुख हुआ। भेजने वाले को तुरंत बताया जाएगा। अपना ध्यान रखिए।",
        "noinfo": "यह जानकारी मेरे पास नहीं है, भेजने वाले को बता दिया जाएगा।",
        "bye_unclear": "थोड़ी देर बाद फिर से कॉल आएगा। नमस्ते।",
    },
    "kn": {
        "family": "ನಿಮ್ಮ ಕುಟುಂಬದ",
        "intro": "ನಮಸ್ಕಾರ! ಇದು ಕೇರ್‌ಕಾಲ್, {sender} ಕಡೆಯಿಂದ ಒಂದು ಜ್ಞಾಪನೆ.",
        "ask": "ನೀವು ಇದನ್ನು ಮಾಡಿದಿರಾ? ಹೌದು ಎಂದು ಹೇಳಿ, ಆಮೇಲೆ ಎಂದು ಹೇಳಿ, ಅಥವಾ ಪ್ರಶ್ನೆ ಕೇಳಿ. ಮಾಡಿದ್ದರೆ 1, ಆಮೇಲೆ ಬೇಕಿದ್ದರೆ 2, ಸಹಾಯ ಬೇಕಿದ್ದರೆ 3 ಒತ್ತಿ.",
        "reask": "ನೀವು ಇದನ್ನು ಮಾಡಿದಿರಾ? ಮಾಡಿದ್ದರೆ 1, ಆಮೇಲೆ 2, ಸಹಾಯಕ್ಕೆ 3 ಒತ್ತಿ.",
        "unclear": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ. ಮಾಡಿದ್ದರೆ 1, ಆಮೇಲೆ 2, ಸಹಾಯಕ್ಕೆ 3 ಒತ್ತಿ.",
        "done": "ಧನ್ಯವಾದಗಳು. ಕಳುಹಿಸಿದವರಿಗೆ ತಿಳಿಸುತ್ತೇನೆ. ನಮಸ್ಕಾರ!",
        "later": "ಸರಿ, {m} ನಿಮಿಷಗಳ ನಂತರ ಮತ್ತೆ ಕರೆ ಮಾಡುತ್ತೇನೆ. ನಮಸ್ಕಾರ!",
        "worrying": "ಕೇಳಿ ಬೇಸರವಾಯಿತು. ಕಳುಹಿಸಿದವರಿಗೆ ತಕ್ಷಣ ತಿಳಿಸುತ್ತೇನೆ. ಜಾಗ್ರತೆ ವಹಿಸಿ.",
        "noinfo": "ನನ್ನ ಬಳಿ ಆ ಮಾಹಿತಿ ಇಲ್ಲ, ಕಳುಹಿಸಿದವರಿಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
        "bye_unclear": "ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಕರೆ ಮಾಡುತ್ತೇನೆ. ನಮಸ್ಕಾರ.",
    },
}


def lang_of(call: dict) -> str:
    """Language actually used for this call (falls back to English if disabled)."""
    lang = (call.get("language") or "en").lower()
    if lang not in LANGS or lang not in config.ENABLED_LANGUAGES:
        return "en"
    return lang


def p(lang: str, key: str, **kw) -> str:
    return PHRASES.get(lang, PHRASES["en"])[key].format(**kw)
