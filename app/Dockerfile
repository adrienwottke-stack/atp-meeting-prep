FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
ARG ATP_COMMIT=unknown
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY docs/handbook ./docs/handbook
COPY scripts/build_handbook.py ./scripts/build_handbook.py
RUN .venv/bin/python scripts/build_handbook.py --revision "$ATP_COMMIT" --output /app/handbook/ATP-Handbuch.pdf

FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
RUN groupadd --gid 10001 atp && useradd --uid 10001 --gid atp --no-create-home atp
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OUTPUT_DIR=/app/output
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "atp.web:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "10.0.1.0/24"]
