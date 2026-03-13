FROM python:3.12 AS builder
WORKDIR /app
RUN pip install poetry
RUN poetry config virtualenvs.in-project true
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app/.venv .venv/
COPY . .
RUN apt-get update -y && apt-get install libpq5 -y && apt-get clean

CMD [".venv/bin/fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8080"]
