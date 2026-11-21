FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pilot pilot
COPY web web
COPY migrations migrations
COPY alembic.ini .
USER 65534:65534
EXPOSE 8000
CMD ["uvicorn","pilot.api:app","--host","0.0.0.0"]
