FROM oven/bun:1 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run check && bun run test && bun run build

FROM python:3.13-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SMP_API_HOST=0.0.0.0
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY smp22x/ ./smp22x/
COPY --from=frontend /build/build ./frontend/build
EXPOSE 8050
CMD ["python", "-m", "smp22x", "dashboard"]

FROM runtime AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests/ ./tests/
CMD ["python", "-m", "unittest", "discover", "-s", "tests"]
