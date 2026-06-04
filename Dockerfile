FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl bash && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip uninstall -y opencv-python || true && \
    pip install --force-reinstall --no-cache-dir "opencv-python-headless>=4.9.0"
COPY . .
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]