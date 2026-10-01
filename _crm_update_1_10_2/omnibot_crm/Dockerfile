FROM python:3.12-slim
WORKDIR /srv/crm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -r -u 10001 crm && chown -R crm:crm /srv/crm
USER crm
CMD ["sh","-c","uvicorn crm.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
