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
heads and available build artifacts; checks the pinned cosign binary and a
Sigstore-audience GitHub OIDC token without creating a transparency-log entry;
and checks feed read and push access.
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
records/<sequence>/record.sigstore.json
records/<sequence>/bundle.tar
```

`record.json` is the canonical JSON ReleaseRecord, ending with a newline.
`record.sigstore.json` is the cosign v3.1.3 keyless bundle for the exact canonical
`record.json` bytes. Cosign signs with the GitHub OIDC identity
`https://github.com/jon-robbins/homelab/.github/workflows/release.yml@refs/heads/main`;
consumers verify that exact identity and issuer
`https://token.actions.githubusercontent.com`. Each signature is also recorded
in Sigstore's public Rekor transparency log. `bundle.tar` is the exact compiled
bundle referenced by `record.json` through `bundle_sha256`.

The `sources` object uses the exact GitHub repository names: `homelab`,
`homelab-site`, and optional `tracker-integrations`.

Publication observes the current `releases` ref and commits the next sequence
with compare-and-swap semantics. A ref race is retried; the workflow never
force-pushes or overwrites an existing sequence. Consumers must verify the
record schema, signature, and bundle digest before using a release.

Before enabling the workflow, the owner creates `PRIVATE_INPUT_READ_TOKEN`
(contents/actions read on `homelab-site` and `tracker-integrations`) and
`SITE_FEED_TOKEN` (contents write on `homelab-site`) in the `production-release`
environment. Cleanup is to delete the old `RELEASE_SIGNING_KEY` secret and
`RELEASE_SIGNING_KEY_ID` variable, and remove `trust/release-keys.json` from
`homelab-site`. No signing secret or trust map is used by the workflow. The
cosign installer action is pinned by commit SHA and its pin is managed by
Dependabot.
