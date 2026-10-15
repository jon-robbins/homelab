# plugin-hello

The `whoami` service shows the request identity. It uses `APP_DATA_DIR` for
its persistent bind. The example site supplies `APP_DATA_DIR` in `site.env`.

After the site renderer has produced `rendered-compose.yml`, run it from the
example site with `docker compose -f rendered-compose.yml up whoami`.
