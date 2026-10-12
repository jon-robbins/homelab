# Releases

**Status: Current.** The release workflow reconciles the main branch on a
15-minute schedule (at minutes 7, 22, 37, and 52) and can also be started
manually. It verifies release inputs, publishes each OCI image through the
protected publisher, signs the release record, and appends the signed record
to the site feed. The workflow is serialized so a slow run is not cancelled by
the next scheduled run.

The plan selects the newest `main` commit absent from the feed, admits only
images present in that commit's successful post-merge build, and records each
image's artifact name, manifest digest, and approved GHCR destination. It also
copies the site's `compose.json`, `manifest.yaml`, `probes.json`, `policy.yaml`,
and `files/` tree at the verified site SHA into the handoff artifact. Signing
compiles only those copied inputs; an empty plan skips publication, signing,
and feed work.

## Feed layout

The feed is the `releases` branch of `homelab-site`. Each release is an
append-only directory under `records/<sequence>/` containing:

```text
records/<sequence>/record.json
records/<sequence>/record.sig
records/<sequence>/bundle.tar
```

`record.json` is the canonical JSON ReleaseRecord, ending with a newline.
`record.sig` is canonical JSON containing `key_id` and a base64-encoded
64-byte Ed25519 signature. The signature covers the release domain followed by
the exact canonical `record.json` bytes. `trust/release-keys.json` in
`homelab-site` maps each key ID to its PEM public key. `bundle.tar` is the exact
compiled bundle referenced by `record.json` through `bundle_sha256`.

The `sources` object uses the exact GitHub repository names: `homelab`,
`homelab-site`, and optional `tracker-integrations`.

Publication observes the current `releases` ref and commits the next sequence
with compare-and-swap semantics. A ref race is retried; the workflow never
force-pushes or overwrites an existing sequence. Consumers must verify the
record schema, signature, and bundle digest before using a release.

Before enabling the workflow, the owner creates these three secrets in the
`production-release` environment: `PRIVATE_INPUT_READ_TOKEN` (contents/actions
read on `homelab-site` and `tracker-integrations`), `RELEASE_SIGNING_KEY`
(visible only to signing), and `SITE_FEED_TOKEN` (contents write on
`homelab-site`). Generate an Ed25519 key, store the private key as
`RELEASE_SIGNING_KEY`, set `vars.RELEASE_SIGNING_KEY_ID`, and commit
`trust/release-keys.json` with the matching public key to `homelab-site`.
No private key is committed.
