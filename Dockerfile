FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PAPERMINT_SOFFICE=/usr/bin/libreoffice \
    PAPERMINT_REQUIRE_LIBREOFFICE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-crosextra-caladea \
        fonts-crosextra-carlito \
        fonts-dejavu-core \
        fonts-liberation \
        fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
