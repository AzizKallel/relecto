# One image for every Relecto service (web app, AI worker, DICOM receiver).
#   WITH_AI=1 (default) installs the open-source image models (PyTorch CPU + TotalSegmentator).
FROM python:3.12-slim
ARG WITH_AI=1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend \
    RELECTO_DATA_DIR=/data TOTALSEG_HOME_DIR=/models
WORKDIR /app
COPY requirements.txt requirements-ai.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN if [ "$WITH_AI" = "1" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
      pip install --no-cache-dir -r requirements-ai.txt ; fi
COPY backend ./backend
COPY clinical-content ./clinical-content
RUN useradd --create-home --uid 1000 relecto && mkdir -p /data /models && chown -R relecto /data /models
USER relecto
EXPOSE 8000 11112
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
CMD ["python", "-m", "relecto", "serve", "--host", "0.0.0.0", "--port", "8000"]
