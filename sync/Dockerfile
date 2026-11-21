# kb-sync image (spec 002, FR-003). Built locally by Compose from ./sync; never pushed.
# Base pinned by digest (python 3.12.16, Debian slim). Re-pin deliberately when upgrading.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/kb-sync

# Hash-pinned dependencies, compiled from pyproject.toml (see its header).
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY kb_sync ./kb_sync
COPY tests ./tests
COPY pyproject.toml .

# uid 1000 matches the owner, so the 0600 secrets and the mounted .env are readable.
RUN useradd --uid 1000 --create-home kbsync
USER 1000:1000

ENTRYPOINT ["python", "-m", "kb_sync"]
