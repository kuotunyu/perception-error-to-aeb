# Reproducing the locked AEB analysis

The formal simulation ran at source commit
`a65ae17ad11be9aee59e8ea341e62c606a588451`. D3 preserves that simulation
identity rather than substituting the later analysis commit. The corresponding
run context records protocol SHA-256
`bbf0b6d31943a0f99160afe1d4c18f9a181850367494004037bab98d8f2e59f9`,
cohort membership SHA-256
`65e38df24b91786fb773b883e1cad4348c0cdc58ac976c729871c77e9478daf9`,
container image configuration ID
`sha256:f6e353acb49e6796541e4204fd34d660596b05f229b193843b34780f8c95d814`,
and configuration `all`.

The preserved D2 validation established 26 configurations, 344 common-valid
tokens, 8,944 token documents, 26,832 measured v2 scenario-replicate records,
and zero invalid tokens. Its immutable launch provenance SHA-256 is
`9c17c5e975ba1c24fb71c90548d5b58cd216760d9946507575117111901137d4`;
the formal validation document SHA-256 is
`3465bfb2644835b06a66eee178bf597a56e43d9c14f874704e8ad29dab766089`.

## Commands and byte identity

Both analysis executions used the pinned Linux container and the same immutable
formal inputs:

```powershell
$env:NUPLAN_DATA_ROOT='D:/datasets/nuplan'
docker compose run --rm dev uv run --frozen aeb-risk evaluate --results-dir artifacts/formal/nuplan_aeb_v2 --manifest artifacts/manifests/nuplan_aeb_v2/evaluation.json --output-dir docs/evidence/nuplan_aeb_v2
docker compose run --rm dev uv run --frozen aeb-risk evaluate --results-dir artifacts/formal/nuplan_aeb_v2 --manifest artifacts/manifests/nuplan_aeb_v2/evaluation.json --output-dir artifacts/d3-reproduction/nuplan_aeb_v2
```

Both commands exited 0 and reported 26 configurations over 344 common-valid
tokens. A recursive relative-path, byte-length, and SHA-256 comparison found
9 files in each directory and **0 differences**. The published hashes were:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `evaluation.json` | 20,052 | `dee735e1b5bed356a9ebf2d17c5aedda7515a31e5a3e7b7be1d97ed48fa3b404` |
| `intervals.json` | 24,862 | `3e4ad4f0979b7e59c4fce1c9c4cec080092ea63799f79bf1ca7c81b9f55281f2` |
| `shapley.json` | 985 | `412a42b49ad42235682dd57b60c53876a53cccc3892c19460d0c5cd8678013f5` |
| `exclusions.json` | 299 | `5a4bc25be368a3f1674204bbe6bc9871597f33f29c3d564320c5856053a9fc13` |
| `cohort/smoke.json` | 780 | `bffc1fb93eb2961a6f84eabd30073eac2059d5828f654f4a172ae6eb40ed2fe1` |
| `cohort/development.json` | 12,694 | `6a71e29b26b940a52820c294c1682bd65fc1af1286fea763f13d46b6b9399c3f` |
| `cohort/evaluation.json` | 18,250 | `76d326690b6ba0666a0db9a452bbb38307877dcf6ba730586838ab6b557762d6` |
| `cohort/development-eligibility.json` | 473,232 | `fbd7fe980b3c28191645197f301eae96277cf3dedeb029927a756a73fe881f88` |
| `cohort/evaluation-eligibility.json` | 1,426,273 | `b9a78c518582c6f60581b56396140a390b29e1611605262ff8bb76fe68174d84` |

The common cohort contains 344 unique tokens. Each configuration row contains
1,032 scenario-replicate records and its separately measured exposure. The
top-level exposure is the explicit sum over the 26 rows: 389,838.9 simulated
seconds. It is not a shared horizon denominator. The exclusion count is zero,
so there are no exclusion reasons to enumerate.

## Statistical boundary

The Shapley reference is `coalition-none`, the zero-severity tracking pipeline,
not `oracle_aeb`. Even at zero severity, tracking derives velocities from
observed position differences; oracle bypasses that pipeline. Contributions
therefore decompose the full coalition minus the empty coalition.

Each interval first averages the three replicates within a token, then performs
5,000 paired, family-stratified resamples with seed 20260831. One token draw is
shared across all configurations. Shapley likewise builds both metric games
from each token's replicate means, checks efficiency to at most `1e-9`, and
only then averages channel contributions. The observed maximum residuals are
`2.220446049250313e-16` for collision indicator and
`2.6645352591003757e-15` for intervention duration.

Those residuals verify arithmetic efficiency, not statistical confidence.
`shapley.json` contains observed mean contributions without uncertainty bounds.
`intervals.json` contains configuration-specific intervals from shared paired
draws, not intervals for differences between configurations. Neither artifact
supports significance claims for changes or rankings of channel importance.

The result record does not carry distance travelled. This release therefore
reports `collisions_per_100km` as `null` for every configuration rather than
inventing a distance denominator.

## License decision

The locally supplied nuPlan `LICENSE` was read in full before publication: 25,319 bytes,
SHA-256 `1a218286e733f6d6135fc5698d614cda2be94ea096f6eee280278458e570636a`.
Its Motional terms dated November 16, 2021 and attached CC BY-NC-SA 4.0 grant
non-commercial sharing of licensed and adapted material, including applicable
database extraction, subject to attribution, ShareAlike, notices, and warranty
terms. The five cohort files contain derived identifiers and selection
measurements, with no embedded separate notice and no sensor or trajectory
payload. They are therefore included with the evidence-specific notice.
