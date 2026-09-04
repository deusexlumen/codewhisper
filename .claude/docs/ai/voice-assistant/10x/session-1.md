# 10x Analysis: Sprachgesteuerter Entwicklungs-Assistent
Session 1 | Date: 2026-09-04

## Current Value
Voice pair-programming companion. User speaks into mic, Gemini Live API answers by voice; Flet window shows status dot + transcript. Duo-Mode alternates two personas (Visionär/Pragmatiker). Background critic silently re-reads the transcript every N turns and injects a logic-error hint via invisible text. Sessions save/load as JSON transcripts. All 4 planned phases shipped.

**Core action**: talk to the model about a coding problem, hear it talk back.
**Where value currently caps out**: everything the assistant "knows" comes only from what the user says out loud. It never sees the actual code, the terminal, a stack trace, or a diff — codebase confirms zero clipboard/screen/file/diff/function-calling code (`grep` across main.py, gemini_session.py, sessions.py, config.py: no hits). It's a smart rubber duck with a voice, not a pair programmer.

## The Question
What turns this from "talks like a dev" into "actually helps you ship code"?

---

## Massive Opportunities

### 1. Code-Context Grounding (clipboard / active file / git diff → live context)
**What**: Before/during a turn, capture user's clipboard, currently-open file, or `git diff` and feed it into the Live session as context (system instruction update or a text turn, same mechanism `duo_mode`/`background_critic` already use via `send_text()`).
**Why 10x**: This is the single biggest gap. Right now the model reasons about code it has never seen — user has to narrate everything verbally, which is slow and lossy. Grounding it in real code turns generic advice into "line 42, your loop never increments i."
**Unlocks**: real debugging, real code review, real "what does this error mean" — the actual pitch of a "Entwicklungs-Assistent."
**Effort**: Medium-High (clipboard read is trivial with `pyperclip`; git diff is a subprocess call; both funnel through existing `send_text()` plumbing).
**Risk**: token/context bloat if diffs are large; needs truncation.
**Score**: 🔥 Must do

### 2. Function-Calling → the assistant can act, not just talk
**What**: Give the Live session tool declarations (`run tests`, `git status`, `read file X`) via `google-genai`'s function calling. Model can request an action, `gemini_session.py` executes it locally (sandboxed to project dir), returns result as a turn.
**Why 10x**: Closes the loop from "advice" to "verified advice." Today the critic already does closed-loop analysis but only on transcript text — this extends that pattern to real command execution. "Run the tests" becomes a spoken sentence with a real result read back.
**Unlocks**: TDD-by-voice, "did that fix work" without touching the keyboard.
**Effort**: High — needs a permission/allowlist model (echoes `spartan:careful`/`spartan:freeze` conventions already in this user's toolkit) so the assistant can't run arbitrary shell commands unsupervised.
**Risk**: safety — must hard-scope allowed commands, never blind `shell.exec`.
**Score**: 👍 Strong (do after #1, and only with a strict allowlist)

### 3. Vision input — point it at the screen
**What**: Gemini Live API supports video frames. Add a "share screen region" toggle that streams a low-fps crop of the editor/terminal into the Live session alongside audio.
**Why 10x**: Skips typing/reading entirely — "what's wrong with this" while looking at the actual error in the terminal. Bigger lift than #1 but strictly more powerful (works for anything, not just clipboard-sized snippets).
**Unlocks**: reviewing UI bugs, reading stack traces, reading whiteboard sketches.
**Effort**: Very High (frame capture + throttling + Flet overlay for region picker).
**Risk**: privacy (screen contains secrets) — needs an explicit, visible "sharing" indicator, never silent.
**Score**: 🤔 Maybe — big payoff, but do #1 first; frequently #1 alone covers most of the same use cases cheaper.

---

## Medium Opportunities

### 1. Cross-session project memory
**What**: `sessions.py` already persists transcripts but `load_session()` is read-only display — CLAUDE.md confirms it "does not resume any live Gemini conversation context." On connect, auto-summarize the last session (or let user pick one) and inject as a short context turn.
**Why 10x**: Without this, every session starts from zero — the assistant re-learns the project's shape every single time. A 2-sentence "last time we were debugging X, landed on Y" primer makes it feel like a continuing collaborator instead of a stranger each morning.
**Impact**: turns disposable chats into an ongoing relationship with the codebase.
**Effort**: Medium (summarization = one extra `generate_content` call, same pattern as `background_critic.check()`).
**Score**: 🔥 Must do

### 2. Critic reads real code, not just transcript
**What**: Extend `background_critic.build_critic_prompt()` to include the same code-context snippet from Massive #1 (current file/diff), not just the last 12 transcript lines.
**Why 10x**: Right now the critic can only catch *logical* inconsistencies in what was *said* — it can't catch "you said you fixed the off-by-one but the diff shows you didn't." Grounding it closes that gap and makes the critic dramatically sharper for near-zero extra plumbing once #1 exists.
**Effort**: Low once Massive #1 lands.
**Score**: 🔥 Must do (bundled with #1)

### 3. Push-to-talk / mic-open indicator with hotkey
**What**: Global hotkey (not just in-window mute button) to toggle mic, plus a much more prominent "I am listening right now" state.
**Why 10x**: Voice assistants live or die on trust that they're not eavesdropping mid-thought or picking up keyboard clatter as speech. A hotkey means user doesn't need window focus to mute.
**Effort**: Medium (global hotkey library + Flet, e.g. `keyboard` package).
**Score**: 👍 Strong

---

## Small Gems

### 1. Auto-save session on disconnect
**What**: `do_save_session` exists but is a manual button (`main.py:189`); `page.on_disconnect` (per CLAUDE.md) only stops audio/session, doesn't save. One-line addition: call `save_session(transcript_log)` in `shutdown()` if log is non-empty.
**Why powerful**: Eliminates the single most annoying failure mode — closing the window and losing the whole conversation because you forgot to click save.
**Effort**: Trivial.
**Score**: 🔥 Must do

### 2. Visible "critic is about to interject" cue
**What**: Tiny UI pulse/icon right before a critic hint gets injected via `send_text()`, so the voice interruption doesn't feel like the AI randomly changing its mind mid-sentence.
**Why powerful**: Costs one status callback, removes a genuine "wait, why did it say that" confusion moment described nowhere but implied by the mechanism (critic hints are invisible text nudges — user has no idea one is coming).
**Effort**: Trivial.
**Score**: 👍 Strong

### 3. One-click "replay last AI answer"
**What**: Button that replays the last buffered `gemini_to_speaker` audio chunk sequence (already sitting in `AudioEngine`'s deque briefly) or re-synthesizes from the transcript.
**Why powerful**: "Wait, what did you just say" is the single most common voice-UI complaint category; near-zero cost if audio is still in memory.
**Effort**: Low-Medium (depends how long chunks persist post-playback).
**Score**: 🤔 Maybe

---

## Recommended Priority

### Do Now (Quick wins)
1. **Auto-save on disconnect** — one line, kills data loss.
2. **Critic-incoming UI cue** — one status callback, kills confusion.

### Do Next (High leverage)
1. **Code-Context Grounding** (clipboard/file/diff → `send_text()`) — the load-bearing 10x move; everything else compounds on top of it.
2. **Critic reads real code** — free upgrade once #1 lands.
3. **Cross-session project memory** — makes every session start warm instead of cold.

### Explore (Strategic bets)
1. **Function-calling for real actions** (run tests/git) — high value, needs a real permission model before shipping.
2. **Vision/screen-share input** — biggest lift, likely superseded in the near term by how good #1 alone feels.

### Backlog (Good but not now)
1. Global hotkey mic toggle — nice, not urgent.
2. Replay-last-answer — nice, low priority vs. grounding work.

---

## Questions

### Answered
- **Q**: Does the assistant currently see any code at all? **A**: No — confirmed via grep, zero clipboard/screen/file/diff/function-calling code anywhere in the repo.
- **Q**: Is there prior-session context reuse? **A**: No — `load_session()` is display-only, doesn't feed back into a live Gemini session (per CLAUDE.md).

### Blockers
- **Q**: For function-calling (Massive #2), what commands should be allowlisted — read-only (`pytest`, `git status`, `git diff`) only, or should write actions (file edits) ever be in scope? Needs user decision before design.
- **Q**: For code-context grounding, prefer clipboard-based (zero IDE integration, works everywhere) vs. active-file-watch (needs to know which editor/file, more setup)? Recommend clipboard-first as the cheapest path to the same value.

## Next Steps
- [ ] Validate: user's actual workflow — do they copy code into clipboard before talking, or is the editor always the same file being discussed?
- [ ] Decide: function-calling permission model (align with existing `spartan:careful`/`spartan:freeze` conventions this user already uses elsewhere).
- [ ] Build: ship the three "Do Now" items first as a trust-building warm-up, then Code-Context Grounding as the real 10x move.
