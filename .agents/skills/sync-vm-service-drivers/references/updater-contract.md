# Incremental VM Service Driver Updater Contract

The audit bundle is immutable evidence for choosing and implementing one protocol revision. Validate
`report.json` against its bundled schema, verify `manifest.json`, confirm the SDK target identity,
and compare plugin precondition hashes before relying on it. Regenerate stale evidence.

## Select one candidate span

The recorded baseline is a historical generation anchor and may be older than the plugin. Determine
the plugin's current version from `VmService.java`. In `change_candidates`, find the candidate that
first transitions into that version; for the baseline version, use the baseline itself. Select every
following candidate up to and including the first candidate whose `protocol_after` differs. That
first new value is the sole target version.

Include same-version candidates inside that span. For example, a documentation or type change made
while the specification still says 4.4 belongs to the 4.4-to-4.5 update if it precedes the commit
that first declares 4.5. Do not include candidates after the target-version transition.

If the current version has no matching entry, the versions are non-monotonic, or there is no later
transition, do not guess. Report that the plugin is up to date or that history is insufficient.

## Use generated output safely

Reconstruct or generate the Java trees at the current-version and target-version commits and compare
that incremental delta with the working tree. The report's net baseline-to-latest readiness is not a
substitute for this incremental comparison when its historical baseline is older than the plugin.

Treat generated additions and changes as the expected API shape, then review them for Java type
safety, nullability, repository conventions, and existing customizations. Preserve plugin-owned
files and intentional deviations. If a selected change overlaps a local customization, stop for a
human decision instead of replacing it.

The candidate span remains fixed through the test-first pauses. Because test and usage sources are
manifest inputs, regenerate and validate a fresh bundle after adding the accepted tests and before
production implementation. Confirm that the new report selects the same span; if the SDK or driver
inputs changed enough to alter it, tell the user and stop rather than silently changing scope.
