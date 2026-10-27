"""Language codes and the names they are known by (#133).

A club's languages are the profile options' languages, each a code and a
name (users R69b, applications R28). Translations are keyed by code. A name
written without a code (as profile options were before #133) takes the code
of the language it names, or ``x-<name>`` for one not known here, so no
language is lost.
"""

import re

ENGLISH = "en"
ENGLISH_NAME = "English"
# A BCP 47-like code: "mr", "kok", "pt-br", or "x-<name>" for a private one.
LANGUAGE_CODE = re.compile(r"^(?:[a-z]{2,3}|x)(?:-[a-z0-9]{1,8})*$")
PRIVATE_PREFIX = "x-"
PRIVATE_NAME_MAX = 8

KNOWN_LANGUAGES: dict[str, str] = {
    "english": "en",
    "marathi": "mr",
    "hindi": "hi",
    "gujarati": "gu",
    "kannada": "kn",
    "konkani": "kok",
    "tamil": "ta",
    "telugu": "te",
    "malayalam": "ml",
    "bengali": "bn",
    "bangla": "bn",
    "punjabi": "pa",
    "odia": "or",
    "oriya": "or",
    "assamese": "as",
    "urdu": "ur",
    "sanskrit": "sa",
    "nepali": "ne",
    "sindhi": "sd",
    "french": "fr",
    "german": "de",
    "spanish": "es",
}


def is_language_code(value: object) -> bool:
    """Whether ``value`` is written as a language code ("x" alone is not)."""
    return isinstance(value, str) and value != "x" and bool(LANGUAGE_CODE.match(value))


def code_for_name(name: str) -> str:
    """The code of a language written by name only."""
    known = KNOWN_LANGUAGES.get(name.strip().lower())
    if known:
        return known
    slug = re.sub(r"[^a-z0-9]", "", name.lower())[:PRIVATE_NAME_MAX] or "lang"
    return PRIVATE_PREFIX + slug
