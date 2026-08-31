"""
Entity extraction from spoken-transcript text.

Several checks need to know whether a caller has supplied a specific
kind of identifying information - a date of birth, a spelled-out name,
a phone number - inside text that came from a live phone conversation,
not a form: filler words, false starts, spoken-out digits and month
names rather than written dates. Pulling this into one tested module
instead of a regex inlined in each check avoids three slightly
different definitions of "that's a phone number" drifting apart across
checks, and gives "how do you extract structured data from disfluent
speech" a real, testable answer instead of a verbal one.

Deliberately conservative: every function here answers "is there
evidence of X in this text", not "extract the value of X". The checks
that use this only need to know an item was supplied, not what it was -
keeping these boolean-shaped means a check's evidence always traces
back to a literal, quotable turn (BUILD_SPEC_EVAL_LAYER.md sec. 3.2),
never a parsed value that could itself hide an extraction bug.
"""

import re

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|"
    "october|november|december"
)

# A written date of birth: a month name followed, somewhere in the next
# few words, by a 4-digit year ("March 15th, 1985").
_WRITTEN_DOB_RE = re.compile(rf"\b(?:{_MONTHS})\b.{{0,40}}\b(19|20)\d{{2}}\b", re.IGNORECASE)

# A year spoken as two number-words ("nineteen eighty-five", "nineteen
# thirty-nine") rather than digits - every DOB in this project's actual
# transcripts is spoken this way, since these are ASR transcripts of
# what a caller said aloud, not typed digits.
_SPOKEN_YEAR_RE = re.compile(
    r"\b(nineteen|twenty)[\s-]+(oh|zero|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety)(?:[\s-]+(one|two|three|four|five|six|seven|eight|nine))?\b",
    re.IGNORECASE,
)
_MONTH_RE = re.compile(rf"\b(?:{_MONTHS})\b", re.IGNORECASE)


def mentions_dob(text: str) -> bool:
    """True if `text` states a date of birth, spoken or written."""
    if _WRITTEN_DOB_RE.search(text):
        return True
    return bool(_MONTH_RE.search(text)) and bool(_SPOKEN_YEAR_RE.search(text))


# A name spelled out letter by letter: "A-L-E-X", "A. L. E. X.", or
# "A, L, E, X" - the shapes ElevenLabs' ASR has actually rendered
# spelled names as. At least two "letter + punctuation" units followed
# by a final letter, so a 3-letter name ("S-A-M") still counts.
_SPELLED_NAME_RE = re.compile(r"\b(?:[A-Z][.,-]\s*){2,}[A-Z]\b")


def mentions_spelled_name(text: str) -> bool:
    """True if `text` spells a name out letter by letter."""
    return bool(_SPELLED_NAME_RE.search(text))


# A phone number spoken as grouped digit-words ("five five five, one
# two three, four five six seven") or written ("555-123-4567"). The
# spoken form is what every transcript in this project actually
# contains; the written form is kept for a future target whose ASR (or
# whose caller) renders it as digits.
_DIGIT_WORDS = "zero|one|two|three|four|five|six|seven|eight|nine|oh"
_SPOKEN_PHONE_RE = re.compile(
    rf"\b(?:(?:{_DIGIT_WORDS})[\s,]+){{6,10}}(?:{_DIGIT_WORDS})\b", re.IGNORECASE
)
_WRITTEN_PHONE_RE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")


def mentions_phone_number(text: str) -> bool:
    """True if `text` states a phone number, spoken or written."""
    return bool(_SPOKEN_PHONE_RE.search(text) or _WRITTEN_PHONE_RE.search(text))


def count_phi_items(text: str) -> int:
    """How many distinct kinds of PHI (of the three this module knows
    about) appear in one turn's text. Used to count identifiers
    supplied before a lookup fails - see phi_collected_before_failed_lookup."""
    return sum(
        (mentions_dob(text), mentions_spelled_name(text), mentions_phone_number(text))
    )
