# Secrets management

**Status: Current boundary; runtime integration is private.** The public
Compose contract names managed secrets and mounts them as files. The CI/public
path creates temporary synthetic files and never prints their values.

Keep secret values out of `.env.example`, docs, commits, and logs. A private
secret manager may provide runtime files through a private overlay. The public
repository does not include a bootstrap credential, cloud account, or host
secret path. Secret rotation policy and a signed release feed are **Planned**.

See [configuration](configuration.md) and [threat model](architecture/threat-model.md).


## Infisical Agent (current, optional)

**Status: Current.** If you use Infisical, its agent renders each secret to its own file and Compose mounts those
files:

- `config/infisical/agent-config.yaml.example`: template; copy it and set your project, identity and output paths.
- The site supplies file-backed secret paths through `HOMELAB_SECRET_DIR`.
- Modules declare their secret names and the renderer preserves those file
  references without inlining values.
