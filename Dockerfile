FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY clinical-content ./clinical-content
RUN useradd --create-home relecto && mkdir -p /app/data/private && chown -R relecto /app/data
USER relecto
EXPOSE 8000
CMD ["python", "-m", "relecto", "serve", "--host", "0.0.0.0", "--port", "8000"]
