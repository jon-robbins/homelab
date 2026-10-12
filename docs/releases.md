# Releases

**Status: Current.** The release workflow reconciles the main branch on a
15-minute schedule (at minutes 7, 22, 37, and 52) and can also be started
manually. It verifies release inputs, publishes each OCI image through the
protected publisher, signs the release record, and appends the signed record
to the site feed. The workflow is serialized so a slow run is not cancelled by
the next scheduled run.

## Feed layout

The feed is the `releases` branch of `homelab-site`. Each release is an
append-only directory under `records/<sequence>/` containing:

```text
records/<sequence>/record.json
records/<sequence>/record.sig
records/<sequence>/bundle.tar
```

`record.json` is the canonical JSON ReleaseRecord, ending with a newline.
`record.sig` is the detached Ed25519 signature over the release domain and the
canonical record bytes. `bundle.tar` is the exact compiled bundle referenced
by `record.json` through `bundle_sha256`.

Publication observes the current `releases` ref and commits the next sequence
with compare-and-swap semantics. A ref race is retried; the workflow never
force-pushes or overwrites an existing sequence. Consumers must verify the
record schema, signature, and bundle digest before using a release.

The `production-release` environment owns the workflow credentials:
`PRIVATE_INPUT_READ_TOKEN` is limited to verification reads,
`RELEASE_SIGNING_KEY` is visible only to signing, and `SITE_FEED_TOKEN` is
visible only to feed publication.
