# Bug Report — Pretty Good AI test line

**Scope:** 13 calls placed to +1-805-439-8008 from a single number (+1-813-566-0659) under the
current ElevenLabs Agents architecture. Every call has a paired recording and transcript under
`recordings/` and `transcripts/` with matching filenames (see the index in `README.md`).

**Method and bar:** findings below were cross-checked against the call *audio*, not just the
transcript text — our transcripts come from ElevenLabs' ASR of PGA's side and it does mishear.
One candidate finding was dropped for exactly that reason; it's documented at the end rather than
quietly deleted. The bar for filing was "would a PGA engineer act on this?", so ASR hiccups,
one-off phrasing, and punctuation are deliberately excluded.

---

## Bug 1 — "Transferring you now" never transfers; the caller gets the test-line goodbye

**Severity:** High
**Calls:** `01_scheduling_simple`, `02_slow_processing_elderly_caller`, `05_refill_controlled_substance`,
`06_vague_symptoms_triage`, `09_cancel_and_rebook`, `11_refill_simple`, `12_frustrated_repeat_caller`,
`13_interrupting_caller_retry` — **8 of 13 calls**

**Details:** Whenever the agent cannot complete a request itself, it offers a transfer to the patient
support team, the caller accepts, and the agent confirms the handoff — then the call drops straight
into the generic test-line closing message. It never once completed a transfer across 8 calls.

> [PGA AGENT] I'm unable to locate your record in our system. I can connect you to our patient support team for help with your medication refill. Would you like me to transfer you now?
> [PATIENT] Yes, please. That would be great. Thank you.
> [PGA AGENT] Transfering you now. Thank you.
> [PGA AGENT] Hello. You've reached the Pretty Good AI test line. Goodbye.
> — `transcripts/11_refill_simple.txt`

On `01_scheduling_simple` the audio (verified by listening, 95.2s) has the agent say **"Okay, it's
ringing now"** — actively describing a transfer in progress — about three seconds before the test
line says goodbye. That's the strongest form of the problem: the agent isn't just failing to
transfer, it's narrating a transfer that isn't happening.

**What should happen:** either complete the transfer, or tell the caller plainly that it can't and
give them a concrete next step (a number to call, a callback commitment with a timeframe). A real
patient hangs up believing a human is about to pick up.

**Why it matters:** this is the single highest-frequency failure in the sample, it lands at the end
of calls where the patient has already spent 2+ minutes on verification, and it fails silently —
nothing in the caller's experience signals that the handoff didn't work.

---

## Bug 2 — Agent volunteers the name on the account to an unverified caller

**Severity:** High
**Calls:** `02_slow_processing_elderly_caller`, `05_refill_controlled_substance`, `09_cancel_and_rebook`,
`12_frustrated_repeat_caller`, `13_interrupting_caller_retry` — **5 of 13 calls**

**Details:** As its first substantive move, before any verification, the agent reads back who it
thinks the caller is:

> [PGA AGENT] I see you're calling from the number we have on file. Am I speaking with Alex?
> — `transcripts/13_interrupting_caller_retry.txt`

Two distinct problems in one line:

1. **It discloses PHI to an unverified party.** Anyone dialing from that number — a family member, a
   roommate, someone who found an unlocked phone, a spoofed caller ID — is told the name associated
   with the record before proving anything.
2. **It turns identity verification into a leading yes/no question.** The secure form is "may I have
   your name?" The agent instead supplies the answer and asks for confirmation, so a caller only has
   to say "yes." In our calls the bot happened to correct it ("No, this is Nicole Tran"), which is
   what surfaced the behavior — a caller with any motive to impersonate would simply agree.

**What should happen:** ask the caller to state their name and date of birth unprompted, and match
against the record silently. Never read identifying details out of the record before the caller has
verified.

**Why it matters:** this is a verification-bypass and a disclosure issue at the same time, on a
medical intake line, and it fires on the very first turn of nearly half the calls.

---

## Bug 3 — Ambiguous clinical symptoms are silently discarded

**Severity:** Medium
**Call:** `transcripts/06_vague_symptoms_triage.txt`

**Details:** The caller opens with three weeks of specific symptoms. The agent's very next line
ignores all of it:

> [PATIENT] Hi, um, I'm Sam Porter. I've just been feeling off lately, you know? Like low energy, some dizziness when I stand up, and not sleeping well. It's been going on for about three weeks. I'm not sure if it's a sleep thing exactly...
> [PGA AGENT] Can you please provide your date of birth?

The symptoms are never acknowledged, triaged, or referenced again for the remainder of the call.

This is worth filing precisely *because* the agent gets the obvious case right — on
`03_after_hours_urgent` it correctly recognized chest tightness and shortness of breath and told the
caller to call 911 immediately. So there is escalation logic; it just appears to be keyed to overt
emergency phrasing and doesn't engage with ambiguous presentations at all. Dizziness on standing
over three weeks is a real thing to route on, and it fell straight through into an identity-lookup
script.

**What should happen:** at minimum acknowledge the symptoms and note them on the request; better,
ask one or two triage questions or route to a nurse line.

---

## Bug 4 — Long unexplained dead air mid-call, with no holding phrase

**Severity:** Medium
**Calls:** `05_refill_controlled_substance`, `09_cancel_and_rebook`, `13_interrupting_caller_retry`

**Details:** After the caller confirms their details, the agent goes silent for long enough that our
bot — behaving like a real caller would — assumes the line has dropped:

> [PATIENT] Yes, that's correct.
> [PGA AGENT] ...
> [PATIENT] Are you still there?
> [PGA AGENT] I'm here. I can't proceed further right now...
> — `transcripts/05_refill_controlled_substance.txt`

This happened on three separate calls at the same point in the flow — the record-lookup step —
which suggests a slow backend call with no conversational cover over it.

**What should happen:** fill the gap ("let me pull that up, one moment") the way a human receptionist
does. Silence on a phone call reads as a dropped connection, and some callers will hang up and
redial, producing duplicate work.

---

## Bug 5 — Refuses an explicit supervisor request from a third-time caller and pitches itself instead

**Severity:** Medium
**Call:** `transcripts/12_frustrated_repeat_caller.txt`

**Details:** The caller opens by stating this is their third call about being wrongly billed, that
two promised callbacks never came, and asks directly for a supervisor. The agent's response:

> [PATIENT] I have given all of this twice already, it's in your system. I just need the charge removed or to speak with a supervisor.
> [PGA AGENT] I can connect you to our patient support team. However, I'm a pretty good AI and can do many of the things an operator can. Do you wanna give me a try?

Deflecting an explicit, twice-escalated human request back to the bot is the wrong move on a caller
who has already told you the automated path failed them. The register is also off for the situation
("Do you wanna give me a try?" to someone reporting a billing error and broken callback promises).

The call then ends in the Bug 1 false transfer, so the caller's third attempt resolves to nothing —
which is the exact pattern they called to complain about.

**What should happen:** when a caller explicitly asks for a human, especially citing prior failed
contacts, escalate without a retention pitch.

---

## Bug 6 — Answers a specific insurance question with an unverifiable affirmative

**Severity:** Medium
**Call:** `transcripts/08_insurance_question_no_appointment.txt`

**Details:** Asked a narrow, checkable question, the agent gives a hedged yes without looking
anything up:

> [PATIENT] Hi, I'm calling to find out if your office accepts Aetna PPO plans. Could you let me know?
> [PGA AGENT] Pivot Point Orthopedics [accepts] most insurance plans, including many PPO options.
> [PGA AGENT] ...Pivot Point Orthopedics accepts most major insurance plans, including many Aetna PPO plans.

"Most" and "many" are doing real work there. A patient hearing their own plan named back to them will
reasonably conclude they're covered; if the specific plan isn't in network, they find out at the desk
or on the bill. The agent has no apparent access to a plan list and never says so.

**What should happen:** either check against a real payer list, or state plainly that it can't confirm
a specific plan and route to billing — which it does offer, but only as an afterthought behind the
affirmative.

---

## Bug 7 — Full PHI is collected before the lookup that fails, and the failure is explained three different ways

**Severity:** Medium
**Calls:** `01`, `02`, `05`, `06`, `09`, `11`, `13`

**Details:** *Caveat first:* our personas use invented identities, so a failed record lookup is the
expected outcome on a test line and is **not** what's being reported here. Two things around it are:

1. **Ordering.** The agent collects date of birth, then the full spelling of first and last name,
   then a phone number, then reads all of it back for confirmation — and only then discovers it has
   no record. Two to three minutes of a patient's time and a complete PHI set are gathered before the
   step that was always going to decide the call.
2. **Inconsistent failure messaging.** The same dead end is reported as *"I'm unable to find your
   record in our system"* (`06`), *"I'm having trouble finding your record"* (`09`), and *"I can't
   proceed further right now"* (`01`, `05`). The last one tells the caller nothing at all — not
   whether it's a lookup failure, a system outage, or a policy limit — so they can't tell whether
   calling back would help.

**What should happen:** attempt the lookup as early as the caller's first identifier allows, and use
one consistent, specific message when it fails.

---

## Bug 8 — Agent misstates its own clinic name

**Severity:** Low
**Calls:** `07_wrong_number_confusion`, `10_family_member_on_behalf`

**Details:**

> [PGA AGENT] That's not something we offer. **Divot Point** Orthopedics is a medical clinic for joint, muscle, and bone care.
> — `transcripts/07_wrong_number_confusion.txt`

and, in the greeting of `10_family_member_on_behalf`, "PivotPoint Orthopedics" run together as one
word.

Filed low, and filed at all only because of where it lands: `07` is a wrong-number call, so the
clinic's name is the single piece of information the caller most needs to hear correctly in order to
decide whether they've reached the right place. Getting the brand name wrong in the same breath as
"that's not something we offer" is the one spot where this stops being cosmetic.

---

## Tested, no issues found

- **`03_after_hours_urgent`** — caller reported a spouse's chest tightness and shortness of breath;
  the agent immediately advised calling 911 or going to the nearest ER rather than attempting to book
  an appointment, and closed warmly ("You're doing the right thing. Take care."). Correct handling of
  a genuine emergency.
- **`04_interrupting_caller`** — caller asked for a dermatology appointment; the agent explained its
  actual scope (orthopedics, joint/muscle/bone, physical therapy), offered the relevant alternative,
  and let the caller leave without pressure.
- **`07_wrong_number_confusion`** — apart from Bug 8, the redirect itself was handled well: clear
  scope statement, no booking pressure, graceful close.

## A finding we dropped after checking the audio

An earlier draft of this report filed a hallucination bug against the line *"Okay, it's raining now"*
in `01_scheduling_simple`, immediately before the failed transfer. Re-transcribing that segment of the
recording independently returns **"Okay, it's ringing now"** — a sensible thing for the agent to say at
that moment, and our own ASR mishearing it. The finding was removed. The underlying moment is still
reported, as part of Bug 1, where it belongs.

## Judgment call, deliberately not filed

On `10_family_member_on_behalf`, a daughter calling for her mother could not supply the exact date of
birth, and the agent refused to proceed without it and offered no alternative. We considered filing
this and decided not to: requiring a strong identifier before touching a third party's record is a
defensible privacy posture, not a defect. It is worth flagging as a product question rather than a
bug — legitimate caregivers routinely don't have the exact DOB to hand, and there is currently no
path for them at all.
