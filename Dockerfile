FROM python:3.15-slim AS build
RUN pip install --no-cache-dir "uv>=0.8,<1"
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# Not the shots extra: Playwright drives a browser the image doesn't have.
RUN uv sync --frozen --no-dev --no-editable --all-extras --no-extra shots --python /usr/local/bin/python

FROM python:3.15-slim
LABEL org.opencontainers.image.title="domus" \
      org.opencontainers.image.source="https://github.com/populii/domus" \
      net.unraid.docker.icon="https://cdn.jsdelivr.net/gh/populii/domus@main/src/domus/app/viewer/icon.png" \
      org.opencontainers.image.description="3D site models for comparing retaining wall options" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    DOMUS_SITE=/site \
    DOMUS_OUTPUT=/site/viewer/data \
    DOMUS_DB=/site/.domus/viewer.db \
    DOMUS_DEMO_DATA=/opt/domus-demo \
    DOMUS_HOST=0.0.0.0 \
    DOMUS_PORT=8765 \
    DOMUS_NO_EXTENSIONS=1
# The made-up demo is built into the image: an empty /site shows it until a site of your own is built.
RUN DOMUS_SITE=/tmp/demo DOMUS_OUTPUT=/opt/domus-demo sh -c 'domus demo /tmp/demo && domus build' \
    && rm -rf /tmp/demo \
    && mkdir -p /site && chown 99:100 /site
# nobody:users, the owner of Unraid's shares.
USER 99:100
VOLUME /site
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import os,urllib.request as u;u.urlopen('http://127.0.0.1:'+os.environ['DOMUS_PORT']+'/healthz',timeout=2)"]
CMD ["domus", "serve"]
