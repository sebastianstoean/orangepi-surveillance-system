#!/usr/bin/env sh
set -eu

if [ "${USE_NGINX_MTLS:-false}" = "true" ]; then
  export APP_PORT="${APP_PORT:-8000}"
  export NGINX_LISTEN_PORT="${PORT:-8443}"

  uvicorn main:app --host 127.0.0.1 --port "$APP_PORT" &
  envsubst '${APP_PORT} ${NGINX_LISTEN_PORT}' \
    < /etc/nginx/templates/nginx.conf.template \
    > /etc/nginx/nginx.conf
  exec nginx -g 'daemon off;'
fi

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
