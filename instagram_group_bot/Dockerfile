# Dockerfile — Instagram Group Moderation Bot (Web Service)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY instagram_group_bot.py app.py ./

# Platforms inject PORT; default to 8080 for local/manual runs
ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
