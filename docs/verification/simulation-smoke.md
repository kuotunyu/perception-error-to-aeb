# Verification: the pipeline, run end to end on real recordings

**NOTHING HERE IS A RESULT.** Six scenarios from nuPlan's mini split are a smoke
test: they show that the study's machinery reads a real recording, drives every
configuration through it, compares them and writes records. The findings this
study will report come from the frozen evaluation cohort, and this document is
what had to work before that run could be trusted.

It is also where a mistake was caught that would have inverted the study's
headline. That is the point of running the pipeline on real data before running
it for real.

## What ran

```bash
docker compose run --rm dev uv run --frozen aeb-risk data freeze \
  --db-root /data/nuplan --protocol configs/protocols/nuplan_aeb_v2.yaml \
  --output-dir artifacts/manifests/nuplan_aeb_v2 --split smoke

docker compose run --rm dev uv run --frozen aeb-risk simulate \
  --protocol configs/protocols/nuplan_aeb_v2.yaml \
  --manifest artifacts/manifests/nuplan_aeb_v2/smoke.json \
  --config-id all --output-dir artifacts/smoke/final --split mini
```

| | |
| --- | --- |
| Cohort | 6 tokens, frozen from `mini`, 2 per family for three families |
| Configurations | all 26 cells of the committed matrix |
| Replicates | 3 |
| Runs | 468, every one valid, none excluded |
| Wall clock | 27 min 23 s |

`bicycle_or_vru` is empty in the smoke: mini holds two bicycle scenarios and the
ego is stationary in both, so neither passes the prefilter. The family is
ordinary in the official splits — 50 development and 44 evaluation tokens — and
its absence here is a fact about mini.

## What the smoke checked

**The actuator envelope holds exactly.** Over all 468 runs the largest
deceleration is 6.000000 m/s² and the largest absolute jerk 5.000000 m/s³, which
are the committed policy's limits. The limiter is in the loop, not bypassed by
the state machine's target.

**A replayed cell reproduces byte for byte.** Re-running `oracle_aeb` and
`dropout-medium` into a fresh directory gives files identical to the first run's,
byte for byte. The corrupted cell is the meaningful one: its error draws are
keyed on the token, channel, severity, replicate and protocol hash, so
reproducing it exactly is what makes an experiment out of a random process.

**Replicates of a corrupted cell differ, and of a baseline do not.** 85 of the
156 (token, configuration) cells have replicates that disagree — every one of
them a corrupted cell, which is the point of drawing a replicate at all. The two
baselines are identical across replicates because nothing in them is drawn.

**Missed and false interventions rise with corruption**, which is the shape the
study's hypothesis predicts and the first time this pipeline has produced these
numbers from anything at all:

| Configuration | Missed | False | Mean intervention |
| --- | ---: | ---: | ---: |
| `no_aeb` | 0 | 0 | 0.0 s |
| `oracle_aeb` (the reference) | 0 | 0 | 8.6 s |
| `coalition-none` | 18 | 60 | 9.5 s |
| `dropout-medium` | 24 | 71 | 9.6 s |
| `latency-high` | 39 | 63 | 8.7 s |
| all four channels at medium | 63 | 63 | 14.2 s |

`coalition-none` is not identical to `oracle_aeb` and is not meant to be: at
severity zero the tracking channel still derives velocity by differencing
observed positions, because differencing is how a real tracker obtains velocity.
The gap between them is a measurable quantity rather than a defect, and it is why
the Shapley baseline is `coalition-none` rather than the oracle.

**Three at-fault collisions occurred, all in one cell.** `latency-low` hits a
vehicle on token `2c0d5d6c08` in all three replicates, at 437 J; neither
`no_aeb` nor `oracle_aeb` hits anything. One token is not evidence of anything —
but it is the pipeline demonstrating that it can produce the outcome the study
exists to measure.

## The mistake this smoke caught

The first full smoke reported **`oracle_aeb` with twelve collisions against
`no_aeb`'s three**. Turning the AEB on appeared to cause collisions.

Every one of the twelve happened at an ego speed of 0.00 m/s. Reading the
striking body's position into the ego's own frame settled it: 4.97 m behind the
ego's centre, 0.01 m off its axis, closing at 6.22 m/s. The agents replay the log
and never react, so an ego that brakes — for a pedestrian, correctly — is then
driven into by the vehicle that was following it in the recording.

The rule that fixes it, and the two consequences that follow from it, are stated
in [the simulation contract](../simulation-contract.md). After it:

| | At-fault collisions | Contacts excluded |
| --- | ---: | ---: |
| `no_aeb`, before | 3 | — |
| `oracle_aeb`, before | 12 | — |
| `no_aeb`, after | 0 | 3 |
| `oracle_aeb`, after | 0 | 27 |

Across the whole 468-run smoke, 3 collisions are attributed to the ego and 652
contacts are excluded. **Without this the study would have published that an AEB
with perfect perception is worse than no AEB at all.**

## What it cost, and what that decides

| Measurement | First run on real data | Now |
| --- | ---: | ---: |
| One prefilter candidate | 38.5 s | 1.9 s |
| One threat assessment | 6.9 ms | 0.15 ms |
| One 150-step baseline run | ~34 s | 1.2 s |
| One 150-step corrupted run | — | 5.5 s |

A real urban frame carries 134 to 227 tracked objects, and the first profile put
90 percent of a simulated scenario inside one function. The three changes — an
arithmetic bound before any geometry, the point-to-edge distances and
separating-axis projections computed in one pass each, and the whole 41-step
rollout computed at once — are all provably result-preserving, and a randomised
test compares the batched rollout against a walked one over 576 combinations of
heading, speed and offset.

One token's 26 cells at 3 replicates is 403 s. Over the frozen evaluation cohort
of 344 tokens that is about 38 hours on one core, or 5 across eight. **Three
replicates is therefore the count**, recorded in `attribution/factorial.py` with
this arithmetic beside it, and running the matrix across cores is the first work
of the formal run.
