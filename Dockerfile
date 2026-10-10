FROM mcr.microsoft.com/playwright/python:v1.51.0-noble@sha256:885e4d785085ec83d4dbfcd36c0870e076d926fc76bd52c7e424545782e98e73
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends xvfb xauth tini wget ca-certificates \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb && rm -rf /var/lib/apt/lists/*
COPY monitor monitor
COPY adapters adapters
COPY admin admin
COPY config config
COPY crawl4ai_run crawl4ai_run
COPY diff_sqlite.py .
COPY legacy/runs legacy/runs
COPY legacy/snapshots.original.db legacy/snapshots.original.db
COPY tests tests
COPY scripts scripts
RUN mkdir -p /app/data && chown -R pwuser:pwuser /app
USER pwuser
ENV PYTHONUNBUFFERED=1 DB_PATH=/app/data/snapshots.db
ENTRYPOINT ["tini", "-g", "--", "xvfb-run", "-a", "--server-args=-screen 0 1440x1000x24 -nolisten tcp"]
CMD ["uvicorn", "monitor.web:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
