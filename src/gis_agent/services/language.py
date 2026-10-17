import re


_ROMAN_URDU_MARKERS = {
    "aap", "acha", "achha", "aur", "bata", "batao", "bataiye", "batayein",
    "chahiye", "fasal", "haan", "hai", "hain", "hoon", "ka", "kab", "kaisa",
    "kaisi", "kahan", "kar", "karein", "karo", "ke", "khet", "ki", "ko", "kya",
    "mausam", "mai", "mein", "mera", "mere", "meri", "mujhe", "nahin", "nahi",
    "pakistan", "pani", "theek", "urdu", "wala", "wali",
}


def detect_language(message: str, fallback: str = "english") -> str:
    """Detect Urdu script, Roman Urdu/Hindi, or English from the latest message."""
    if any("\u0600" <= character <= "\u06ff" for character in message):
        return "urdu"

    words = set(re.findall(r"[a-z]+", message.casefold()))
    if not words:
        return fallback
    if words & _ROMAN_URDU_MARKERS:
        return "hinglish"
    return "english"
