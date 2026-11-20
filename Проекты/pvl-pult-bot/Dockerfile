FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY lead_api.py /app/lead_api.py

EXPOSE 8085

CMD ["uvicorn", "lead_api:app", "--host", "0.0.0.0", "--port", "8085"]
