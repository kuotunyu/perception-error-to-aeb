# Container base image resolution

Every result this project produces comes out of one image. A tag can be
re-pointed at different bytes without notice, so the `FROM` line names a
digest and this file records where that digest came from.

## Resolution

Resolved on 2026-09-03 (UTC) against Docker Engine 29.6.1.

```console
$ docker buildx imagetools inspect python:3.9.19-slim-bookworm --format '{{.Manifest.Digest}}'
sha256:69e712dbe4c4a166527cbf69374533125cfb6ee93a5e39031a0191c741d386d7
```

| Field | Value |
| --- | --- |
| Reference | `python:3.9.19-slim-bookworm` |
| Media type | `application/vnd.oci.image.index.v1+json` |
| Index digest, used in `FROM` | `sha256:69e712dbe4c4a166527cbf69374533125cfb6ee93a5e39031a0191c741d386d7` |
| `linux/amd64` manifest digest | `sha256:70fbdeb75b0d071778ea1df4353ac959118ae8031bc5be33a95d1b8ea1fb8f08` |
| `linux/amd64` manifest size | 1942 bytes |

The `FROM` line pins the **index** digest rather than the `linux/amd64`
manifest digest. Both identify immutable content; the index digest additionally
keeps the build working on an arm64 host, which the amd64 manifest digest would
break with a platform mismatch. The amd64 digest is recorded above so that a
future reader can confirm which concrete image an x86-64 build resolved to.

## Other pinned inputs

| Input | Pin | Why |
| --- | --- | --- |
| uv | `ghcr.io/astral-sh/uv:0.11.18`, copied into the image | Same uv series as P1 and P2; uv 0.12 changed lock behaviour mid-portfolio |
| Python | `3.9.19` from the base image, `UV_PYTHON_DOWNLOADS=never` | nuPlan at the pinned commit is a 3.9 codebase; the interpreter must be the image's, never one uv fetches |
| Project dependencies | `uv.lock`, installed with `uv sync --frozen` | A drifted lock fails the build instead of resolving something else |
| nuPlan devkit | git commit `e9241677997dd86bfc0bcd44817ab04fe631405b` | A branch or tag can move; a commit cannot |

## Re-resolving

Do not update the digest to "get fixes". Changing the base image changes the
environment every result was produced in, so it is a protocol change: resolve
the new digest with the command above, record it here beside the old one with
the date and the reason, and re-run the full verification gate before any
result is compared across the boundary.
