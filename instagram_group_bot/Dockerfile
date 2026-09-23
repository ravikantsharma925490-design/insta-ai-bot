# Dockerfile — Instagram Group Moderation Bot (Web Service)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything in the build context (including ig_session.json if present)
COPY . .

# Platforms inject PORT; default to 8080 for local/manual runs
ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
