# nuPlan-derived evidence notice

The JSON files in this directory are compact derived evidence from **nuPlan
v1.1**, created from Motional's dataset with nuPlan devkit commit
`e9241677997dd86bfc0bcd44817ab04fe631405b`.

The `cohort/` files retain scenario tokens, log names, selection measurements,
and acceptance or refusal reasons. The five top-level JSON documents transform
the selected evaluation cohort into aggregate safety measurements, paired
bootstrap intervals, exact Shapley attribution, family intervention event
summaries, and exclusions. No database, map, sensor media, point cloud, raw
trajectory export, or model artifact is included.

The `replays/` directory contains at most twelve selected, derived HTML
timelines: one predeclared median-nearest token from each non-empty family,
shown under three replicate-zero configurations. Geometry is translated to a
scenario-local display origin and native actor identifiers are replaced with
stable anonymous labels. The files contain time, AEB state, visibility, and
derived geometry needed for the display; they contain no sensor pixels, map
view, nuBoard log, database path, log name, or native scenario token. They are
illustrations of the selected formal runs rather than raw trajectory exports.

This derived evidence is shared for non-commercial use under
[Creative Commons Attribution-NonCommercial-ShareAlike 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
and the [Motional dataset terms](https://www.nuscenes.org/terms-of-use).
Copyright and creator notices supplied with nuPlan remain with Motional and the
identified contributors. The material is supplied without warranties under
those terms. Motional does not sponsor or endorse this project.

The replay viewer embeds [Plotly.js](https://github.com/plotly/plotly.js), which
is distributed under the MIT license. Its notice remains in each self-contained
HTML file.

The repository's independently authored source code remains under the
repository's MIT license; that license does not replace the terms governing
this derived evidence.
