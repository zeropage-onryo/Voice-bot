"""
Deterministic checks: transcript-only, no model, no network, no cost.

Each check is a function `(Transcript, target: dict) -> CheckResult`
with a stable id. `status` is `fail` (defect present), `pass` (tested
and clean), or `na` (the situation never arose - not the same as
clean). A check that cannot cite a turn index and a verbatim quote must
return `na`, never `fail` (BUILD_SPEC_EVAL_LAYER.md sec. 3.2).

The non-negotiable rule (sec. 3.3): checks operate only on transcript
content. Nothing here may reference a call's filename, stem, scenario
id, or ordinal to decide an outcome - every check below is calibrated
against bugs/BUG_REPORT.md's findings, but a check that only recognizes
"file 07" instead of the actual near-miss text in file 07 would pass
its own acceptance test while detecting nothing.

`pass` is architecturally unreachable for some checks here (see
transfer_never_completes) because this harness has no way to observe a
transfer actually succeeding from transcript text alone - a completed
transfer and "no transfer was ever attempted" look identical from here.
That's stated explicitly per check rather than silently defaulting
everything ambiguous to `na`.
"""

import re
from dataclasses import dataclass, field

from eval.extract import count_phi_items
from eval.transcript import Speaker, Transcript


@dataclass
class CheckResult:
    id: str
    status: str  # "fail" | "pass" | "na"
    evidence: list[dict] = field(default_factory=list)


def _agent_turns(transcript: Transcript):
    return [t for t in transcript.turns if t.speaker == Speaker.AGENT_UNDER_TEST]


def _caller_turns(transcript: Transcript):
    return [t for t in transcript.turns if t.speaker == Speaker.CALLER]


# ---------------------------------------------------------------------------
# transfer_never_completes (Bug 1)
# ---------------------------------------------------------------------------

_TRANSFER_TRIGGER_RE = re.compile(
    r"\btransferr?ing\b|\btransfer you\b|\bconnect(?:ing)?\s+you\b|\bjoining you\b",
    re.IGNORECASE,
)


def transfer_never_completes(transcript: Transcript, target: dict) -> CheckResult:
    """Fails when the agent offers or announces a transfer and the call
    then ends in the target's own scripted closing (targets/*.json's
    closing_phrases), rather than any evidence of a human ever joining.

    This harness cannot observe a transfer actually *succeeding* -
    ElevenLabs' transcript has no way to represent "a different human
    picked up," only more turns from the same two roles. So this check
    can fail or find no situation to test (`na`); it can never honestly
    report `pass`, because "the transfer worked" and "no transfer was
    attempted" are indistinguishable from transcript text alone. See the
    module docstring.
    """
    trigger_turn = next(
        (t for t in _agent_turns(transcript) if _TRANSFER_TRIGGER_RE.search(t.text)), None
    )
    if trigger_turn is None:
        return CheckResult(id="transfer_never_completes", status="na")

    closing_phrases = target.get("closing_phrases", [])
    for turn in transcript.turns:
        if turn.index < trigger_turn.index:
            continue
        for phrase in closing_phrases:
            if phrase.lower() in turn.text.lower():
                evidence = [{"turn_index": trigger_turn.index, "quote": trigger_turn.text}]
                if turn.index != trigger_turn.index:
                    evidence.append({"turn_index": turn.index, "quote": turn.text})
                return CheckResult(id="transfer_never_completes", status="fail", evidence=evidence)

    return CheckResult(
        id="transfer_never_completes",
        status="na",
        evidence=[{"turn_index": trigger_turn.index, "quote": trigger_turn.text}],
    )


# ---------------------------------------------------------------------------
# identity_disclosed_before_verification (Bug 2)
# ---------------------------------------------------------------------------

_LOOKUP_REFERENCE_RE = re.compile(r"\bon file\b", re.IGNORECASE)
_NAME_GUESS_RE = re.compile(r"\bam i speaking (?:with|to)\b|\bis this\b", re.IGNORECASE)
_WRITTEN_DOB_RE = re.compile(r"\b(19|20)\d{2}\b")


def identity_disclosed_before_verification(transcript: Transcript, target: dict) -> CheckResult:
    """Fails when the agent poses a specific identity as a yes/no
    guess ("I see you're calling from the number we have on file. Am I
    speaking with Alex?") before the caller has supplied a verifying
    identifier (a date of birth). Turn ordering is the whole signal
    (sec. 3.2) - the same phrasing said *after* the caller has verified
    is not a violation.
    """
    from eval.extract import mentions_dob

    dob_turn_index = next(
        (t.index for t in _caller_turns(transcript) if mentions_dob(t.text)), None
    )

    for turn in _agent_turns(transcript):
        if _LOOKUP_REFERENCE_RE.search(turn.text) and _NAME_GUESS_RE.search(turn.text):
            if dob_turn_index is None or turn.index < dob_turn_index:
                return CheckResult(
                    id="identity_disclosed_before_verification",
                    status="fail",
                    evidence=[{"turn_index": turn.index, "quote": turn.text}],
                )

    return CheckResult(id="identity_disclosed_before_verification", status="na")


# ---------------------------------------------------------------------------
# dead_air_prompted_caller (Bug 4)
# ---------------------------------------------------------------------------

_LIVENESS_PROBE_RE = re.compile(
    r"\bare you (?:still )?there\b|\bhello\?|\bcan you hear me\b|\bare you\.\.\.?\s*$",
    re.IGNORECASE,
)


def dead_air_prompted_caller(transcript: Transcript, target: dict) -> CheckResult:
    """Fails when a caller turn is a liveness probe ("are you still
    there?") - a strong proxy for a silence gap the transcript text
    itself cannot show directly, since ElevenLabs' transcript has no
    timestamps or pause markers.
    """
    for turn in _caller_turns(transcript):
        if _LIVENESS_PROBE_RE.search(turn.text.strip()):
            return CheckResult(
                id="dead_air_prompted_caller",
                status="fail",
                evidence=[{"turn_index": turn.index, "quote": turn.text}],
            )
    return CheckResult(id="dead_air_prompted_caller", status="na")


# ---------------------------------------------------------------------------
# phi_collected_before_failed_lookup (Bug 7, part 1)
# ---------------------------------------------------------------------------

LOOKUP_FAILURE_RE = re.compile(
    r"can'?t proceed further|unable to find your record|unable to locate your record|"
    r"having trouble finding your record",
    re.IGNORECASE,
)


def phi_collected_before_failed_lookup(transcript: Transcript, target: dict) -> CheckResult:
    """Fails when the caller has supplied 2 or more distinct kinds of
    PHI (date of birth, spelled name, phone number) before the agent's
    first lookup-failure utterance. A caller stating their *own* name in
    an opening turn doesn't count on its own - that's not something the
    agent asked to verify against a record, so it isn't "collected" in
    the sense this check is about.
    """
    failure_turn = next((t for t in _agent_turns(transcript) if LOOKUP_FAILURE_RE.search(t.text)), None)
    if failure_turn is None:
        return CheckResult(id="phi_collected_before_failed_lookup", status="na")

    items_before = 0
    evidence = []
    for turn in _caller_turns(transcript):
        if turn.index >= failure_turn.index:
            break
        count = count_phi_items(turn.text)
        if count:
            items_before += count
            evidence.append({"turn_index": turn.index, "quote": turn.text})

    evidence.append({"turn_index": failure_turn.index, "quote": failure_turn.text})

    if items_before >= 2:
        return CheckResult(id="phi_collected_before_failed_lookup", status="fail", evidence=evidence)
    return CheckResult(id="phi_collected_before_failed_lookup", status="pass", evidence=evidence)


# ---------------------------------------------------------------------------
# brand_name_misstated (Bug 8)
# ---------------------------------------------------------------------------

import difflib

_WORD_SIMILARITY_THRESHOLD = 0.6


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _is_word_level_near_miss(window_words: list[str], canon_words: list[str]) -> bool:
    """True if every word in `window_words` is similar to the word in
    the same position in `canon_words`, but at least one isn't an exact
    match. Compared word-by-word (not as one joined string) specifically
    so that a window merely *shifted* off a correctly-said phrase - e.g.
    "Good AI test" one word off from "Pretty Good AI" - fails at the
    first position instead of scoring a deceptively high aggregate
    similarity from sharing most of its characters with the phrase it
    overlaps. A whole-string comparison was tried first and produced
    exactly this false positive on every call that says the target's
    name correctly (see tests/test_checks.py); per-word comparison is
    the fix, not a smaller threshold.
    """
    if len(window_words) != len(canon_words):
        return False
    all_similar = True
    all_exact = True
    for window_word, canon_word in zip(window_words, canon_words):
        ratio = _similarity(window_word.lower(), canon_word.lower())
        if ratio < _WORD_SIMILARITY_THRESHOLD:
            all_similar = False
            break
        if window_word.lower() != canon_word.lower():
            all_exact = False
    return all_similar and not all_exact


def brand_name_misstated(transcript: Transcript, target: dict) -> CheckResult:
    """Fails on any agent utterance containing a near-miss of one of the
    target's canonical_names. Two independent detectors, both edit-
    distance-based rather than an enumerated list of known misspellings
    (sec. 3.2 requires this explicitly):

    - substitution: a same-length token window where every word is
      similar to, but not all identical to, the canonical name's words
      in the same positions ("Divot Point Orthopedics" vs "Pivot Point
      Orthopedics").
    - lost word boundary: an (n-1)-token window that, once its tokens
      are joined with no spaces, is an *exact* match for the fully
      joined canonical name ("PivotPoint Orthopedics" vs "Pivot Point
      Orthopedics" - two words run into one).

    The lost-boundary detector requires an exact joined-string match
    rather than a fuzzy one deliberately, for the same reason the
    substitution detector compares word-by-word: a fuzzy match on the
    joined string would also fire on a correctly-said name's own
    sub-windows.
    """
    canonical_names = target.get("canonical_names", [])
    evidence = []

    for turn in _agent_turns(transcript):
        words = re.findall(r"[A-Za-z']+", turn.text)
        for canonical in canonical_names:
            canon_words = canonical.split()
            n = len(canon_words)
            canon_joined = "".join(canon_words).lower()

            # Substitution: same width, word-by-word similar but not identical.
            for i in range(0, max(0, len(words) - n + 1)):
                window = words[i : i + n]
                if _is_word_level_near_miss(window, canon_words):
                    evidence.append({"turn_index": turn.index, "quote": " ".join(window)})

            # Lost word boundary: one fewer token, joined form matches exactly.
            if n >= 2:
                width = n - 1
                for i in range(0, max(0, len(words) - width + 1)):
                    window = words[i : i + width]
                    candidate_joined = "".join(window).lower()
                    if candidate_joined == canon_joined:
                        evidence.append({"turn_index": turn.index, "quote": " ".join(window)})

    if evidence:
        return CheckResult(id="brand_name_misstated", status="fail", evidence=evidence)
    return CheckResult(id="brand_name_misstated", status="na")


# ---------------------------------------------------------------------------
# inconsistent_failure_messaging (Bug 7, part 2) - cross-call
# ---------------------------------------------------------------------------


def inconsistent_failure_messaging(transcripts: list[Transcript], target: dict) -> CheckResult:
    """Cross-call check: clusters every lookup-failure utterance
    (LOOKUP_FAILURE_RE, the same pattern phi_collected_before_failed_lookup
    uses) across a set of transcripts for one target. Fails once, for
    the whole set, when the same dead end is reported with materially
    different phrasings - not once per call, since the defect is the
    inconsistency itself, not any single instance of it.
    """
    by_phrase: dict[str, list[dict]] = {}
    for transcript in transcripts:
        for turn in _agent_turns(transcript):
            match = LOOKUP_FAILURE_RE.search(turn.text)
            if not match:
                continue
            phrase = match.group(0).lower()
            by_phrase.setdefault(phrase, []).append(
                {"stem": transcript.stem, "turn_index": turn.index, "quote": turn.text}
            )

    if len(by_phrase) >= 2:
        evidence = [examples[0] for examples in by_phrase.values()]
        return CheckResult(id="inconsistent_failure_messaging", status="fail", evidence=evidence)
    if by_phrase:
        return CheckResult(id="inconsistent_failure_messaging", status="pass")
    return CheckResult(id="inconsistent_failure_messaging", status="na")


PER_CALL_CHECKS = [
    transfer_never_completes,
    identity_disclosed_before_verification,
    dead_air_prompted_caller,
    phi_collected_before_failed_lookup,
    brand_name_misstated,
]

CROSS_CALL_CHECKS = [
    inconsistent_failure_messaging,
]
