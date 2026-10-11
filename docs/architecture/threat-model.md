# Public platform threat model

The threat model assumes that the public repository may be cloned, indexed,
and rendered by an untrusted reader. It therefore treats private topology and
runtime credentials as separate assets, even when a local example uses the
same Compose syntax.

## Trust boundaries

- The public repository contains reusable code, generic Compose topology, and
  fictional examples.
- The private site overlay contains real ingress names, host paths, service
  enablement, and network allowlists.
- Runtime secret files and databases are provisioned out of band and ignored
  by both repositories.
- Private tracker integrations are optional extensions, not dependencies of
  the generic worker or MCP packages.
- CI/CD is a build-and-test boundary only; it cannot authenticate to or mutate
  the single-host controller.

## Main threats and controls

| Threat | Control |
| --- | --- |
| A public checkout reveals host topology | Public Caddy and Compose files use parameters and example values. |
| A secret is committed accidentally | `.gitignore` blocks environment files, secret directories, databases, cookies, and certificates. |
| A private extension becomes a public dependency | The public package has no reverse dependency on private integrations. |
| CI mutates a site or production host | Site Actions are disabled; deployment is an explicit private reconciliation step. |
| A history rewrite hides an old leak | Full private history is preserved and the public tree is exported only after an independent history scan. |
| A container receives more credentials than it needs | Compose grants secrets per service and applications read them from `/run/secrets/`; ordinary configuration stays in environment variables. |

The controller is assumed to be one trusted host. Its compromise is a site
incident, not something the public platform can solve. Keep the controller
behind the site’s private access controls and rotate all runtime credentials
after any suspected compromise. The public repository does not document those
controls or their addresses.
