FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

ENV PORT=5000
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

# Single worker + threads so the bot's background state is shared with Flask.
# gunicorn.conf.py starts the Discord bot inside the worker process.
CMD ["gunicorn", "-w", "1", "--threads", "4", "-t", "120", \
     "-c", "gunicorn.conf.py", \
     "--bind", "0.0.0.0:5000", "--access-logfile", "-", "app:app"]
