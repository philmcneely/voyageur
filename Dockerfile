# voyageur in a container, on Alpine. The image runs the service only: no browser, no testbed, no test suite, no pip.
#
#   docker build -t voyageur .
#   docker run --rm -p 127.0.0.1:8765:8765 -e HEAL_SERVICE_TOKEN=<32+ characters> -v heal-data:/data voyageur
#
# Inside a container the service must listen on 0.0.0.0, which the CLI allows only with --allow-remote and a token of at
# least 32 characters, so the image always needs HEAL_SERVICE_TOKEN. Publish the port on 127.0.0.1 (as above and in
# compose.yaml) unless a TLS reverse proxy sits in front of it.

# ---- build: the locked dependencies and the package into /app/.venv -----------------------------------------------------
FROM python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89 AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# google-re2 has no musl wheel: it is compiled here against Alpine's re2 and abseil; the runtime stage gets the libraries only
RUN apk add --no-cache g++ re2-dev abseil-cpp-dev py3-pybind11-dev
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra mcp --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra mcp --no-editable

# ---- run: the venv only, as a user with no shell and no home -------------------------------------------------------------
FROM python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
# the base's own packages brought up to the latest Alpine security fixes when the image is built, the re2 library the compiled
# google-re2 links against, and no pip: the service never installs anything at run time
RUN apk upgrade --no-cache && apk add --no-cache re2 libstdc++ \
 && rm -rf /usr/local/lib/python3.12/site-packages/pip /usr/local/lib/python3.12/site-packages/pip-* /usr/local/bin/pip*
RUN addgroup -S -g 10001 heal \
 && adduser -S -D -H -u 10001 -G heal -s /sbin/nologin heal \
 && mkdir /data && chown heal:heal /data
COPY --from=build /app/.venv /app/.venv
COPY LICENSE /app/LICENSE
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HEAL_DB=/data/voyageur.db
USER heal
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["voyageur"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765", "--allow-remote"]
