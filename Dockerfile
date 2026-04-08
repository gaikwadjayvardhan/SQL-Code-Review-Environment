FROM python:3.11-slim

RUN pip install uv --no-cache-dir

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync

COPY . .

EXPOSE 7860

ENV PORT=7860

CMD ["uv", "run", "server"]
