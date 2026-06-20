FROM python:3.13-alpine

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY main.py ./

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD kill -0 1 || exit 1

CMD ["python", "main.py"]
