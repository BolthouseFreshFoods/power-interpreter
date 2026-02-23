FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Cache bust: 2026-02-23 v2.0 — force full pip reinstall
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create sandbox directories
RUN mkdir -p /app/sandbox_data /app/uploads /app/temp /app/logs

# Expose port
EXPOSE 8000

# No healthcheck in Dockerfile - Railway handles it via railway.toml
# Use Python start script to properly read PORT env var
CMD ["python", "start.py"]
