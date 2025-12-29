FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py
COPY settings.py /app/settings.py
COPY app /app/app

# Data (DB + logs) should be mounted as a volume to /data
VOLUME ["/data"]

CMD ["python", "main.py"]


