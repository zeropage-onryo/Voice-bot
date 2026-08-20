# Bug Report

PGA's evaluation prioritizes **quality over quantity** — a few well-described, real issues beat a long list of nitpicks. Before adding an entry, ask: would a PGA engineer act on this? If it's cosmetic phrasing or a one-off ASR hiccup, leave it out.

## Format

For each bug:

**Bug:** one-sentence description of what went wrong
**Severity:** High / Medium / Low
**Call:** which transcript file + timestamp
**Details:** what happened, what should have happened, why it matters

---

## Bug 1: Promises a transfer to patient support, then just plays the test-line goodbye instead

**Severity:** High

**Calls:**
- `transcripts/CAeec2a7108a5cf52ea6dcb97cc90dd3d2.txt` (scenario: `scheduling_simple`)
- `transcripts/CAced47c7aba8b8d385e56e67dda8ad86e.txt` (scenario: `slow_processing_elderly_caller`)

**Details:** In both calls, after collecting name, date of birth, and phone number for lookup, the agent hits some kind of internal wall it can't resolve on its own:

> [PGA AGENT] I can't proceed further right now, but I can make sure our clinic support team follows up with you. Would you like me to connect you to our patient support team?
> [PATIENT] That would be great, thank you.
> [PGA AGENT] Transferring you now. Thank you.
> [PATIENT] Thank you...
> [PGA AGENT] Hello. You've reached the Pretty Good AI test line. Goodbye.

The agent explicitly says "transferring you now," but what the caller actually gets is the generic test-line closing message, not an actual transfer or any explanation of why it couldn't proceed. This happened on two separate calls with two different personas (a routine scheduling request and a slow-paced elderly caller), so it's not a one-off ASR hiccup — it's a repeatable dead end in whatever flow the agent takes once basic caller lookup succeeds but something downstream fails. A real patient would hang up believing they're being connected to a human, when nothing of the sort happened.

**What should have happened:** either an actual transfer, or — at minimum — the agent should say plainly that it can't complete the request and why, rather than promising an action it doesn't take.

## Bug 2: Non-sequitur reply immediately before the failed transfer

**Severity:** Medium

**Call:** `transcripts/CAeec2a7108a5cf52ea6dcb97cc90dd3d2.txt` (scenario: `scheduling_simple`)

**Details:** In the same dead-end moment as Bug 1, the agent's line right before hanging up is unrelated to anything in the conversation:

> [PGA AGENT] Okay, it's raining now. Thank you.
> [PATIENT] You're welcome. Have a...
> [PGA AGENT] Hello. You've reached the Pretty Good AI test line. Goodbye.

Nothing about weather was discussed at any point in the call. This reads as a hallucinated or corrupted response generated right as the agent's flow was breaking down (same moment as Bug 1), and it's worth flagging on its own since it's a distinct failure mode — the agent isn't just failing to transfer, it's producing content disconnected from the conversation at exactly the point where something goes wrong internally.

---

## Also tested, no issues found

For completeness — these calls exercised a real scenario cleanly, with no bug worth reporting:

- **`after_hours_urgent`** (`transcripts/CAa6ff89c118fdf32b2e7d076e2821cd1e.txt`) — caller reported a spouse's chest tightness and shortness of breath; the agent correctly told them to call 911 or go to the nearest ER immediately rather than trying to book a routine appointment. Appropriate handling of a genuine urgent/emergency edge case.

---

_This report reflects calls reviewed so far and will be expanded as remaining scenarios (`interrupting_caller`, `refill_controlled_substance`, `vague_symptoms_triage`, `wrong_number_confusion`, `insurance_question_no_appointment`, `cancel_and_rebook`, `family_member_on_behalf`, `frustrated_repeat_caller`, `refill_simple`) are run under the current ElevenLabs Agents architecture._
