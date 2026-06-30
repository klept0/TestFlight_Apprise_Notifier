FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create a non-root user and give it ownership of the app + data directory so
# the process (and the in-app .env editor) can write where it needs to.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Expose FastAPI port
EXPOSE 8080

# Set default environment variables (can be overridden)
ENV FASTAPI_HOST=0.0.0.0 \
    FASTAPI_PORT=8080

# Health check (uses stdlib urllib so no extra dependency is required)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/health', timeout=5).status == 200 else 1)"

# Run the application
CMD ["python", "main.py"]
