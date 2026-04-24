# Legacy Archive Manifest (2026-04-24)

This archive captures legacy PR1918/ground-truth artifacts moved out of active workflows during the Big Cleanup refactor.

## Archived Artifacts

- `ground-truth/horror_movies.folders.json`
  - Archived from: `ground-truth/horror_movies.folders.json`
  - Reason: ad-hoc one-off dataset, not part of reproducible runtime workflow.

- `scripts/build-seerr-pr1918.sh`
  - Archived from: `scripts/build-seerr-pr1918.sh`
  - Reason: PR1918 preview image build helper is legacy and no longer part of the default setup path.

## Related References Retained (not archived)

- `scripts/migrate-seerr-arr-hosts.py`
- `scripts/tests/test_migrate_seerr_arr_hosts.py`

These remain in-place because they still provide migration/verification value for users who keep a Seerr PR1918 clone.
