FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 yuki

COPY . .
RUN mkdir -p /workspace && chown -R yuki:yuki /app /workspace
USER yuki

ENV YUKI_WORKSPACE=/workspace
CMD ["python", "main.py"]
