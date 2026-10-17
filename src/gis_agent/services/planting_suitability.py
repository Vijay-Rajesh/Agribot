import re


_ENGLISH_SUITABILITY = re.compile(
    r"\b(?:"
    r"(?:can|could|should|would)\s+i\s+(?:grow|plant|cultivate)\b|"
    r"(?:is|are)\s+.{1,60}\s+(?:suitable|suited|appropriate|recommended)\s+"
    r"(?:for|to\s+(?:grow|plant))\b|"
    r"(?:will|can)\s+.{1,50}\s+(?:grow|thrive)\s+(?:here|there|in\s+this\s+"
    r"(?:area|field|farm|soil|climate))\b|"
    r"(?:plant|grow|cultivate)\s+.{1,50}\s+(?:here|there|in\s+this\s+"
    r"(?:area|field|farm|soil|climate))\b"
    r")",
    re.IGNORECASE,
)
_ROMAN_SUITABILITY = re.compile(
    r"\b(?:"
    r"(?:kon|kaun|koun)\s+(?:kon\s+)?se\b.{0,100}"
    r"(?:lag[a-z]*|ug[a-z]*)\s+ja\s+(?:sakte|skte|sakta|skta|sakti|skti)\b|"
    r"(?:kya\s+)?(?:yeh|ye|is|mere|meri)\b.{0,80}"
    r"(?:yahan|is\s+(?:zameen|zamin|khet|field|farm|jagah)|mere\s+(?:khet|farm))"
    r".{0,50}(?:lag|ug|grow|plant|suitable|theek)|"
    r"(?:lag|ug)(?:a|e)?\s+s(?:a)?k(?:te|ta|ti|t)\b|"
    r"(?:lagana|ugana)\s+(?:chahiye|theek|munasib|suitable)\b|"
    r"(?:yahan|is\s+(?:zameen|zamin|khet|field|farm|jagah)).{0,60}"
    r"(?:lag|ug)(?:a|e)?\s+s(?:a)?k(?:te|ta|ti|t)\b|"
    r"(?:grow|plant)\b"
    r")",
    re.IGNORECASE,
)
_URDU_SUITABILITY_MARKERS = (
    "لگا سکتا",
    "لگا سکتی",
    "لگا سکتے",
    "اگ سکتا",
    "اگ سکتی",
    "اگا سکتا",
    "اگانا چاہیے",
    "لگانا چاہیے",
    "یہاں لگ",
    "یہاں اگ",
    "زمین میں لگا",
    "زمین میں اگ",
    "موزوں ہے",
    "مناسب ہے",
)


def is_planting_suitability_request(message: str) -> bool:
    """Identify explicit crop/planting suitability questions."""
    if any(marker in message for marker in _URDU_SUITABILITY_MARKERS):
        return True
    return bool(
        _ENGLISH_SUITABILITY.search(message)
        or _ROMAN_SUITABILITY.search(message)
    )


def planting_suitability_image_instructions(language: str) -> str:
    """Return image-specific instructions that distinguish planting advice from diagnosis."""
    instructions = {
        "english": (
            "This image question is about whether a named plant, fruit, or crop can be planted "
            "on the pictured land. This is a PLANTING SUITABILITY assessment, NOT crop disease "
            "detection. Do not use any disease/condition diagnosis template, symptom checklist, "
            "or treatment/prevention advice. Do not claim the field is suitable just because it "
            "looks prepared. First give a preliminary suitability verdict, then describe only "
            "visible land features relevant to planting, explain that a photo cannot reveal soil "
            "pH, texture, salinity, drainage, irrigation, location, or climate, and list the key "
            "missing information (especially location and planting month/season). Ask concise "
            "follow-up questions when needed. Reply in the user's language and writing system."
        ),
        "hinglish": (
            "Yeh image wala sawal is baat par hai ke user ka bataya hua plant, fruit ya crop "
            "tasveer wali zameen mein lag sakta hai ya nahi. Yeh PLANTING SUITABILITY ka jaiza "
            "hai, fasal ki disease detection NAHI. Disease/condition diagnosis template, "
            "symptoms checklist, ya ilaaj/bachao ke mashware bilkul na dein. Sirf zameen tayyar "
            "nazar aane par use suitable na kahein. Pehle mumkin munasibat ka wazeh verdict "
            "dein; phir sirf zameen ki nazar aane wali planting-related baatein batayein. Saaf "
            "kahein ke photo se mitti ka pH, texture, salinity, drainage, pani/irrigation, "
            "location ya climate maloom nahi hota. Khaas taur par location aur planting month/"
            "season ki kami batayein aur zaroori follow-up sawal poochein. User ki language "
            "aur writing style mein jawab dein."
        ),
        "urdu": (
            "تصویر کے ساتھ سوال یہ ہے کہ صارف کا بتایا ہوا پودا، پھل یا فصل اس زمین میں لگ "
            "سکتی ہے یا نہیں۔ یہ زمین میں کاشت کی موزونیت کا جائزہ ہے، فصل کی بیماری کی "
            "تشخیص نہیں۔ بیماری/مسئلے کی تشخیصی ترتیب، علامات کی فہرست، یا علاج اور بچاؤ کے "
            "مشورے ہرگز نہ دیں۔ صرف زمین تیار دکھائی دینے کی بنیاد پر اسے موزوں نہ کہیں۔ پہلے "
            "ابتدائی موزونیت کا واضح فیصلہ دیں، پھر صرف زمین کی نظر آنے والی متعلقہ خصوصیات "
            "بتائیں۔ واضح کریں کہ تصویر سے مٹی کا pH، ساخت، نمکیات، نکاسی، آبپاشی، مقام یا "
            "آب و ہوا معلوم نہیں ہوتی۔ خاص طور پر مقام اور کاشت کے مہینے/موسم کی معلومات "
            "پوچھیں۔ صارف کی زبان اور رسم الخط میں جواب دیں۔"
        ),
    }
    return instructions.get(language, instructions["english"])
