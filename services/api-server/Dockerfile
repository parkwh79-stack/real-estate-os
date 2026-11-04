FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# OCR is local and offline at runtime. Keep the language models out of source
# control and verify their immutable upstream versions during the build.
COPY parsing_platform/ocr_language_data.py ./parsing_platform/ocr_language_data.py
COPY scripts/prepare_ocr_languages.py ./scripts/prepare_ocr_languages.py
RUN python -m scripts.prepare_ocr_languages --directory /opt/realytics/tessdata
ENV REALYTICS_OCR_TESSDATA_DIR=/opt/realytics/tessdata \
    OMP_THREAD_LIMIT=1

RUN addgroup --system realytics \
    && adduser --system --ingroup realytics --home /home/realytics realytics

COPY --chown=realytics:realytics . .

USER realytics

# The release image must execute OCR, not merely contain language filenames.
RUN python -m scripts.verify_ocr_runtime

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health/live', timeout=3).read()"

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
