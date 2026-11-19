FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install --yes --no-install-recommends bubblewrap ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 bioquorum \
    && useradd --uid 10001 --gid 10001 --create-home bioquorum

WORKDIR /build
COPY pyproject.toml README.md LICENSE THIRD_PARTY.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir . \
    && rm -rf /build/src /build/pyproject.toml

WORKDIR /workspace
RUN chown bioquorum:bioquorum /workspace
USER bioquorum:bioquorum
ENTRYPOINT ["bq"]
CMD ["--help"]
