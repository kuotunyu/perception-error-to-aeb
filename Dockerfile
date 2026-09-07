# syntax=docker/dockerfile:1.7
#
# The one environment every P3 result is produced in. The base image is named
# by digest, not tag, so the image is the same bytes wherever and whenever it
# is built; the digest and the command that resolved it are recorded in
# docs/verification/container-base.md. uv is copied from its own pinned
# release image rather than installed from a script. The project is synced
# from the committed lock with --frozen; CI separately runs `uv lock --check`
# because `--frozen` consumes an existing lock without proving freshness.
FROM python:3.9.19-slim-bookworm@sha256:69e712dbe4c4a166527cbf69374533125cfb6ee93a5e39031a0191c741d386d7

COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/

# git: nuPlan is installed from a commit URL. build-essential: a few of the
# geospatial dependencies compile a small extension on 3.9.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git build-essential \
    && rm -rf /var/lib/apt/lists/*

# Results are produced by an unprivileged user. /work is the repository
# (bind-mounted by compose), /work/artifacts its only writable output root,
# /data holds read-only dataset mounts, /opt/venv the locked environment so it
# never lives on a bind mount.
RUN groupadd --gid 1000 aeb \
    && useradd --uid 1000 --gid aeb --create-home --shell /bin/bash aeb \
    && mkdir -p /work/artifacts /data/nuplan /opt/venv \
    && chown -R aeb:aeb /work /opt/venv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.9 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_SYNC=1 \
    PATH=/opt/venv/bin:/home/aeb/.local/bin:/usr/local/bin:/usr/bin:/bin

USER aeb
WORKDIR /work

# The bind-mounted repository is owned by the host user; git must be told the
# directory is safe or the private guard cannot read the index inside the container.
RUN git config --global --add safe.directory /work

# Dependencies first, so editing source never invalidates the dependency layer.
COPY --chown=aeb:aeb pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --all-groups --no-install-project

# Then the project itself.
COPY --chown=aeb:aeb src ./src
RUN uv sync --frozen --all-groups

CMD ["python", "-c", "import aebrisk; print('perception-error-to-aeb container ready')"]
