FROM python:3.11-slim

RUN pip install uv --no-cache-dir

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY . .

EXPOSE 7860

ENV PORT=7860

CMD ["uv", "run", "server"]
