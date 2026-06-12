# syntax=docker/dockerfile:1

# Base pinneada por digest (reproducible e inmune a re-tags del registro). Para
# actualizarla: `docker pull python:3.12-slim` y reemplazar el sha. La reducción de CVEs
# se delega al escaneo Trivy en CI; ver DECISION_LOG ADR-16.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

# ----------------------------- Builder -----------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
# Instalación reproducible y verificada por hash: las dependencias de runtime están
# pinneadas en requirements.txt (exportado de uv.lock; ver DECISION_LOG ADR-19), así que
# `--require-hashes` falla si una wheel no coincide con su hash. El proyecto se instala
# después sin re-resolver dependencias (ya están todas presentes).
COPY requirements.txt pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip \
    && pip install --require-hashes --no-deps -r requirements.txt \
    && pip install --no-deps .

# ----------------------------- Runtime -----------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Aplica parches de seguridad del SO sobre la base (reduce CVEs corregibles) y crea un
# usuario sin privilegios para ejecutar la aplicación.
RUN apt-get update \
    && apt-get -y --no-install-recommends upgrade \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app
# Config, migraciones y entrypoint Python (sin shell): el entrypoint aplica
# `alembic upgrade head` y luego ejecuta el CMD.
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY docker_entrypoint.py ./docker_entrypoint.py

USER appuser
EXPOSE 8000

ENTRYPOINT ["python", "docker_entrypoint.py"]
CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
