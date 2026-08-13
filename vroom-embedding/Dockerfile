FROM python:3.11-slim

WORKDIR /app

# curl is only needed for the container healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

EXPOSE 8080

# --factory so configuration is read (and the provider probed) at app build
# time; a bad config or a wrong-dimension provider then fails startup loudly.
CMD ["uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
