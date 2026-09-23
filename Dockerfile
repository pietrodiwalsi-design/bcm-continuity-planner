# Dockerfile for the web workshop tool (src/bcm_planner/web/).
#
# The existing MCP server (src/bcm_planner/mcp_server.py) is run locally via
# Claude Desktop's stdio transport and is NOT deployed by this image — this
# Dockerfile only builds the second, separate public web deliverable
# described in docs/web_workshop_tool.md, deployed on Render (see
# render.yaml). Both share the same bcm_planner business-logic modules
# from the same source tree; nothing here duplicates or forks that logic.
#
# Multi-stage: a "builder" stage installs into a venv (keeps the final
# image free of build-only tooling), then a slim runtime stage copies just
# the venv + source across.
#
# WeasyPrint (PDF export) needs Pango/HarfBuzz/fontconfig at the OS level
# for text shaping — confirmed present as system packages on this dev host
# (libpango-1.0-0, libpangoft2-1.0-0, libpangocairo-1.0-0, libcairo2) via
# `dpkg -l`; a slim python base image does NOT ship these, so both stages
# below install the runtime .so packages explicitly via apt.

FROM python:3.11-slim AS builder

# ca-certificates: pip/https. build-essential: any sdists without wheels.
# libpango* etc: WeasyPrint's text-shaping/rendering dependencies (needed
# even at "build" time here only insofar as some Python packages probe
# for them at import; the authoritative install is in the runtime stage
# below, this is just so `pip install` itself doesn't warn/fail).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src

# Installs the core package plus the `web` optional-dependencies group
# (FastAPI, Jinja2, uvicorn, WeasyPrint — see pyproject.toml). The MCP-only
# `fastmcp` core dependency stays installed too since it lives in the same
# `bcm_planner` package tree, but is unused at runtime by this image.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[web]"


FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user (defense in depth; this is a public-facing service).
RUN useradd --create-home --shell /bin/bash bcmweb

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY schema ./schema
COPY scripts ./scripts
COPY src ./src
COPY pyproject.toml README.md ./

RUN chown -R bcmweb:bcmweb /app
USER bcmweb

# Render sets $PORT at runtime and expects the service to bind to it (see
# render.yaml and src/bcm_planner/web/app.py's main() which reads $PORT).
ENV PORT=8000
EXPOSE 8000

# Runs pending schema migrations (schema/run_migrations.py — idempotent,
# safe to run on every deploy, see that script's docstring for why this
# is needed instead of docker-entrypoint-initdb.d on Render's managed
# Postgres) and then starts the web app.
CMD ["sh", "-c", "python scripts/run_migrations.py && python -m bcm_planner.web.app"]
