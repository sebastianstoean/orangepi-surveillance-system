FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends nginx gettext-base \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY static ./static
COPY nginx/nginx.conf /etc/nginx/templates/nginx.conf.template
COPY start_with_nginx.sh .

RUN chmod +x /app/start_with_nginx.sh

CMD ["/app/start_with_nginx.sh"]
