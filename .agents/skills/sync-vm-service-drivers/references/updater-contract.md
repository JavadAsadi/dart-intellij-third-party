# VM Service Driver Updater Contract

The audit bundle is an input contract for a future update mode of this same skill. That mode is deliberately not implemented yet.

Before changing source, validate `report.json` against the bundled `report.schema.json`, verify every `manifest.json` hash, confirm the target SDK identity, and compare every plugin precondition hash. Stop if any check fails; regenerate the audit instead of using stale evidence.

SDK commits in `change_candidates` are ordered evidence, not feature boundaries. Group related candidates into one meaningful user-visible or protocol-level feature, explain that feature to the human, and wait for “next.” Then propose applicable unit, integration, and mocked test strategies and wait for the human to choose.

For each approved feature:

1. Write the chosen test first and demonstrate the intended red state. Prefer a compilable failing test with mocks or fakes. If the missing Java API makes compilation impossible, explain that expected compile failure and proceed only after human acceptance.
2. Implement only that feature, using the report's normalized target output as evidence rather than blindly replacing the driver tree.
3. Preserve all plugin-owned files and locally customized generated logic. If `overlapping_changes` is non-empty, stop for a human decision instead of overwriting it.
4. Run the selected tests, Java compilation, and `VmServiceTest`.
5. On failure, explain the cause, correct it, and retry before selecting another feature.
6. Once the feature passes, summarize it and wait before advancing.

After every feature is complete, run final validation, update `references/baseline.json` to the pinned target specification and SDK commit, and rerun the audit. Completion requires an `up_to_date` report with valid hashes.
