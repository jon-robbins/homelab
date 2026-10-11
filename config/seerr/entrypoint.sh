#!/bin/sh
set -e

if [ -z "${TMDB_API_KEY:-}" ] && [ -f /run/secrets/TMDB_API_KEY ]; then
  TMDB_API_KEY="$(tr -d '\r\n' </run/secrets/TMDB_API_KEY)"
  export TMDB_API_KEY
fi

# Seerr ships a shared TMDB v3 key; patch with your own when TMDB_API_KEY is set.
if [ -n "${TMDB_API_KEY:-}" ]; then
  TMDB_FILE="/app/dist/api/themoviedb/index.js"
  if [ -f "${TMDB_FILE}" ]; then
    sed -i "s/431a8708161bcd1f1fbe7536137e61ed/${TMDB_API_KEY}/g" "${TMDB_FILE}"
  fi
fi

# Seerr bug: ImageProxy always registers customProxyAgent.requestInterceptorFunction.
# When HTTP proxy is disabled that export is undefined, and Axios image fetches fail
# with ETIMEDOUT → blank posters / Failed to proxy image. Use a no-op interceptor.
IMAGEPROXY_FILE="/app/dist/lib/imageproxy.js"
if [ -f "${IMAGEPROXY_FILE}" ]; then
  sed -i \
    's/this\.axios\.interceptors\.request\.use(customProxyAgent_1\.requestInterceptorFunction);/this.axios.interceptors.request.use((config) => config);/' \
    "${IMAGEPROXY_FILE}"
fi

exec /usr/local/bin/docker-entrypoint.sh npm start
