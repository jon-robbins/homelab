# Releases

**Status: Current.** The release workflow reconciles the main branch on a
15-minute schedule (at minutes 7, 22, 37, and 52) and can also be started
manually. It verifies release inputs, publishes each OCI image through the
protected publisher, signs the release record, and appends the signed record
to the site feed. The workflow is serialized so a slow run is not cancelled by
the next scheduled run.

To exercise the production credentials without writing anything, run the live
check on `main`:

```sh
gh workflow run release.yml -f mode=check
```

The check runs one protected job per credential. It reads the site and tracker
heads, trust map, and available build artifacts; proves the signing key against
the trust map with a throwaway message; and checks feed read and push access.
Scheduled runs always use reconcile mode.

## Release authority policy

The public `jon-robbins/homelab` source is released only from a commit on
`main` that is in the workflow run's `github.sha` ancestry. The plan job uses
the workflow's `github.token` to require that the `main` ruleset is active,
targets `main`, requires pull requests, blocks force-push and branch deletion,
and has no bypass actors. The plan does not use an owner-merged-PR API check
for this public repository.

`jon-robbins/homelab-site` and `jon-robbins/tracker-integrations` are private
repositories on GitHub Free, so they have no rulesets. Their `main` heads must
still be merge commits from pull requests merged into `main` by a configured
release owner. A non-owner merge, wrong base, direct commit, or mismatched merge
SHA is denied.

Each OCI archive is attested in the `post-merge-build.yml` workflow with
`actions/attest-build-provenance`, pinned by commit SHA. The build job alone
has `id-token: write` and `attestations: write`; all other jobs retain their
existing permissions. During planning, every downloaded OCI archive is checked
with:

```text
gh attestation verify <archive> --repo jon-robbins/homelab \
  --signer-workflow jon-robbins/homelab/.github/workflows/post-merge-build.yml \
  --source-digest <release-sha>
```

Thus the signed artifact, signer workflow, and source commit must all match
the release before its manifest digest is admitted.

The plan selects the newest `main` commit absent from the feed, downloads OCI
artifacts from the Actions artifact store, and admits only archives whose
native attestation verifies. The manifest digest is read from the verified
archive's `index.json`; no custom provenance file participates in selection or
digest admission. The plan records each image's artifact name, download run ID,
manifest digest, and approved GHCR destination. It also
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
