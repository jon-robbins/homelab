# Secrets management

**Status: Current boundary; runtime integration is private.** The public
Compose contract names managed secrets and mounts them as files. The CI/public
path creates temporary synthetic files and never prints their values.

Keep secret values out of `.env.example`, docs, commits, and logs. A private
secret manager may provide runtime files through a private overlay. The public
repository does not include a bootstrap credential, cloud account, or host
secret path. Secret rotation policy and a signed release feed are **Planned**.

See [configuration](configuration.md) and [threat model](architecture/threat-model.md).


## v2 secret-file flow

**Status: Current.** Populate a private directory with one file per managed
secret, then set `HOMELAB_SECRET_DIR` to that directory when rendering the
modules. Compose mounts the files directly into the services that declare
them; values are never placed in `.env.example`, documentation, or Compose
environment entries.

The required filenames are the secret names declared by the modules, such as
`TMDB_API_KEY`, `QBITTORRENT_PASSWORD`, and `SONARR_API_KEY`. A private secret
manager may render or refresh those files; the public repository does not
prescribe its credentials, agent configuration, or host integration.
