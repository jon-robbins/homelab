# 0009: Digest pins, allowlisted namespaces, attested first-party images

- **Status:** Accepted (2026-10-12; 2026-10-15)
- **Code:** `config/release/image-policy.json`, `config/release/approved-ghcr-destinations.json`,
  `.github/workflows/post-merge-build.yml`, `.github/workflows/reusable-publish.yml`, `.github/dependabot.yml`
- **Related:** [0004](0004-signed-full-state-releases.md), [0014](0014-dependabot-automerge.md)

## Decision

- Every image is pinned by digest (`repo:tag@sha256:…`). The tag is for humans and Dependabot.
- Upstream and plugin images must come from a namespace in `image-policy.json` and resolve in their registry.
  The reviewed pin in a merged PR is the trust anchor.
- First-party images (caddy, homelab-workers) are built post-merge with GitHub artifact attestations; the release
  verifies the attestation, byte-copies the image to its published name and injects the digest. The digest in a
  first-party module file is a placeholder that the release replaces.
- GitHub Actions are pinned by full SHA. Nobody types a SHA: Dependabot bumps them, pinact writes version
  comments, zizmor and actionlint run in pre-commit and CI.

## Consequences

- A plugin repo's images may only come from its own namespace.
- With one author, plugin images are not signed: the builder and the reviewer are the same person. When someone
  else writes plugins, add cosign key-pair signing with a `signing_key` in `homelab-plugin.yaml`.

## Don't

- Don't use a tag without a digest, or `latest`, anywhere.
- Don't add a namespace to `image-policy.json` as part of adding a service; make it its own change.
