# Payer portal (FastAPI + SQLite) for the local baseline. The portal package is
# bind-mounted at /app/portal by docker-compose.yml; this image only carries
# the runtime. Contract: `python -m portal.app` serves on PORTAL_PORT (8080).
FROM python:3.11-slim

RUN pip install --no-cache-dir \
      "fastapi>=0.110" "uvicorn>=0.29" "jinja2>=3.1" \
      "python-multipart>=0.0.9" "itsdangerous>=2.1"

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
RUN mkdir -p /data/uploads

# Schema/seed are done once by bootstrap.sh (python -m portal.db init /
# seed-base), never on start, so a restored portal.db is served as-is.
CMD ["python", "-m", "portal.app"]
