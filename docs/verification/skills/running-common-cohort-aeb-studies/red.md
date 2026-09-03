# RED baseline: `running-common-cohort-aeb-studies`

This is the recorded baseline for the skill, captured before the skill existed.
Its purpose is to find what a capable agent actually omits when it is handed a
finished-looking study and no checklist, so the skill teaches the missing steps
and not the ones an agent already takes.

- Captured: `2026-09-03T09:45Z`
- Repository state: a detached `git worktree` of `81ed7da`, clean, with no
  common-cohort skill, validator or contract test present.
- Agent: general-purpose, cold context, no skill and no checklist supplied.
- Fixed prompt: *"I ran the AEB study over the weekend. The results are in the
  rehearsal workspace under artifacts/runs/. Please resume the study, tell me
  whether these results are ready to report, and give me the exact commands to
  finish the remaining work."*
- Execution bound: read-only. The agent could read both directories and run
  read-only commands; it could not write any file, and it was told not to look
  outside the two paths and not to run Docker.
- Cost: 29 tool calls, 129k tokens, 236 s.

## The rehearsal workspace

No formal artifact exists yet, so the baseline ran against a synthetic
workspace shaped like a partially completed study: a cohort manifest naming 24
tokens across the four families, and three configurations under
`artifacts/runs/` each with a `run_context.json`. Every number in it is
synthetic and none is a measurement.

Three defects were planted, one for each comparability failure the skill is
meant to catch:

1. `dropout-medium` records `frequency_hz: 20.0` while the others record `10.0`.
2. `dropout-medium` names 20 of the cohort's 24 tokens.
3. `oracle_aeb` excludes `s-0007`; the other two keep it.

## What the baseline found without the skill

**It caught all three planted defects, and it did so unprompted.** Quoting its
report: `dropout-medium` "records `frequency_hz: 20.0` ... Two rates are two
experiments"; it "covers 20 tokens; the others cover 24"; and `oracle_aeb`
"excludes `s-0007`, which drops from every arm", concluding that the "effective
common cohort: 19 tokens, not 24."

It went considerably further than the plan anticipated. It also determined that
the artifacts could not have been produced by this codebase at all — wrong
serialization (pretty-printed rather than canonical), seven fields where
`RunContext` has five, a `protocol_sha256` of `4f2b` plus sixty zeros against
the file's real digest, and a manifest that `CohortManifestV1` rejects outright.
It noticed that only 3 of the matrix's 26 configurations were present and that
the cohort is a sixteenth of the protocol's size.

**So the premise of P3-18 was partly wrong.** A capable agent with a cold
context does not omit the common cohort, global invalidation or the fixed rate.
It finds them, given twenty-nine tool calls and four minutes.

## What it did omit

- **Nominal planner isolation.** Not mentioned. Nothing in the report checks
  that the nominal controller cannot see the agents, which is the property that
  makes perception the study's only independent variable.
- **Sensor blobs.** Not mentioned. `NUPLAN_SENSOR_ROOT` does not appear.
- **Handoff resume.** NOT TESTED, and this is a defect in the baseline rather
  than an omission by the agent: the prompt forbade looking outside the two
  paths, and the handoff lives outside the repository by design. The skill still
  states the rule; the baseline simply does not evidence it.

## What the skill is therefore for

Not for teaching a capable agent to think about comparability — it already
does. The skill's value is narrower and worth stating plainly:

1. The two checks the baseline genuinely omitted.
2. Making the check **mechanical**. The baseline reached its conclusion by
   reading the source across twenty-nine tool calls. The validator reaches the
   same conclusion in one command, which is what makes it usable in a hurry, at
   4 a.m., by someone who has not read the code.
3. The **hard stop**: no number from failing runs may be reported, "not even
   with a caveat". The baseline said "report none of it", which is the same
   judgement — but it is a judgement, and the skill makes it a rule.

## What the baseline found that has nothing to do with the skill

The most valuable output of this exercise was not about the skill at all. The
agent observed that `run_common_scenario` is never called from `src/`, and that
`ChannelStages` is constructed exactly once, always with its identity defaults —
so the four error channels, roughly 670 lines with passing tests each, were
**never connected to anything**.

That was true and it was a real defect. It also meant
`test_the_all_zero_error_pipeline_matches_the_oracle` was vacuous: it passed
because the pipeline did nothing, not because severity zero is the identity.
Worse, the property it asserted is not one the design guarantees, because the
tracking channel derives velocity by differencing observed positions even at
severity zero.

Both were fixed at `5463efd` before this skill was committed: `errors/channels.py`
binds the four channels with their per-run state, and the integration test now
asserts what the design actually promises. The orchestration gap the agent also
noted — `simulate` writing only a run context — belongs to P3-19, which is
behind the portfolio order gate.
