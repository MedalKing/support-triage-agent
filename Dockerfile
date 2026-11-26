# The public demo: the API and the built web page in one container.
#   docker build -t support-triage-agent .
#   docker run -p 8000:8000 -e ANTHROPIC_API_KEY -e DEMO_ACCESS_CODE=... support-triage-agent

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Public defaults: only Claude (no Ollama in the cloud), and trust the host's proxy for visitor addresses.
    DEMO_PROVIDERS=auto,claude \
    DEMO_TRUST_PROXY=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY support_agent/ support_agent/
COPY evals/results/ evals/results/
RUN pip install --no-cache-dir . && useradd --uid 10001 --no-create-home app
COPY --from=web /web/dist web/dist
USER app
EXPOSE 8000
# --app-dir puts /app first on the path, so the app finds web/dist next to it.
CMD ["sh", "-c", "exec uvicorn support_agent.api:app --app-dir /app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
