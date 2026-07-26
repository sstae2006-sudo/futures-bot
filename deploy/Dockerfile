# Runtime image for the Phase 5 CLI-only live-trading bot (`futures_bot.cli
# --live`) -- a single strategy, one broker connection, no dashboard, no
# API. For the FastAPI research API + React dashboard (Phases 6-8B:
# backtests, optimizer, the Live Session paper-trading page, the
# autonomous Research Server), see `Dockerfile.api` instead -- a different
# product, built and deployed separately. See DEPLOYMENT.md.
#
# Two things here exist specifically because this process handles money:
#
#   * It runs as a non-root user. A trading bot needs no privileges beyond
#     reading its config and writing its state.
#   * /data is declared a volume. The kill switch lives there. On a container
#     host, anything not on a mounted volume is erased on restart — and a
#     kill switch that forgets a halt when the container bounces is not a
#     kill switch. See DEPLOYMENT.md.

FROM python:3.12-slim AS base

# Unbuffered so logs reach the host's collector in real time rather than
# sitting in a buffer while the market moves.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency layer first, so source edits don't trigger a full reinstall.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --shell /bin/bash bot \
    && mkdir -p /data/state /data/logs \
    && chown -R bot:bot /data /app

USER bot

# State and logs both belong on the volume. State is the important one.
VOLUME ["/data"]

ENV FUTURES_BOT_CONFIG=/app/config.yaml

# Fails the container if settings are invalid, rather than letting a
# misconfigured bot sit idle looking healthy.
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -m futures_bot.cli --config "$FUTURES_BOT_CONFIG" --check > /dev/null || exit 1

ENTRYPOINT ["python", "-m", "futures_bot.cli"]
CMD ["--config", "/app/config.yaml", "--check"]
