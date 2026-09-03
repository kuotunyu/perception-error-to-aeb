# Two independent builds install the same environment

Every result this project produces comes out of one image. Pinning the base by
digest and the dependencies by lock is only a claim until two builds that share
no cache are shown to install the same distributions.

## Method

```console
$ docker compose build --no-cache
$ docker compose run --rm dev bash -lc '/opt/venv/bin/python -c "
import importlib.metadata as m
for name, version in sorted((d.metadata[\"Name\"], d.version) for d in m.distributions()):
    print(name + \"==\" + version)
"'
```

Run twice, on 2026-09-03, against Docker Engine 29.6.1. `--no-cache` is what
makes the second build independent: every layer, including the `uv sync` that
resolves the environment, is executed again rather than reused.

## Result

| | Build 1 | Build 2 |
| --- | --- | --- |
| Distributions installed | 58 | 58 |
| Manifests differ | — | no |

The two manifests are byte-identical. SHA-256 of either:
`49617422375b4b033f3a8ab93d680b14f6f75c4ef65159ddf721fe15634c07a6`.

Selected pins, as a spot check against the lock:

| Package | Version |
| --- | --- |
| `perception-error-to-aeb` | 0.1.0 |
| `nuplan-devkit` | 1.2.2 (from commit `e9241677997dd86bfc0bcd44817ab04fe631405b`) |
| `numpy` | 1.23.4 |
| `SQLAlchemy` | 1.4.27 |
| `shapely` | 2.0.7 |
| `pydantic` | 2.13.5 |

## What this does and does not show

It shows that the lock and the pinned base image determine the installed
environment, so two people building this image on different days get the same
one. It does not show bit-for-bit identical image layers: timestamps and
filesystem ordering differ between builds, and reproducing those would need a
different toolchain. The environment is what a result depends on, and that is
what is pinned.

Re-run this whenever `uv.lock`, the base-image digest, or the `Dockerfile`
changes, and record the new manifest hash here beside the old one.
