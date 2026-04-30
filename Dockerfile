FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "pinebitz.api:app", "--host", "0.0.0.0", "--port", "8000"]
