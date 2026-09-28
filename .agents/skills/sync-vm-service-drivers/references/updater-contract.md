# Incremental VM Service Driver Updater Contract

The audit bundle is immutable protocol evidence for choosing and implementing one revision. Validate
`report.json` against its bundled schema, verify `manifest.json`, confirm the SDK target identity,
and compare plugin precondition hashes before relying on it. Regenerate stale evidence.

The audit does not generate Java, predict a production patch, or determine that an existing driver
matches the specification. The LLM owns that analysis and every source edit.

## Select one candidate span

The recorded baseline is a historical specification anchor and may be older than the plugin.
Determine the plugin's current version from `VmService.java`. In `change_candidates`, find the
candidate that first transitions into that version; for the baseline version, use the baseline
itself. The first following candidate whose `protocol_after` differs identifies the sole target
version. Select every candidate after the current-version anchor through the final consecutive
candidate whose `protocol_after` still equals that target version. Stop before the first candidate
that transitions beyond the target version. If the target is the latest recorded version, include
all remaining candidates that continue to declare it.

Include same-version candidates on both sides of the transition into the target. For example, a
documentation or type change made while the specification still says 4.4 belongs to the 4.4-to-4.5
update if it follows the 4.4 anchor, and a compatibility fix that still declares 4.5 also belongs to
that update. This produces the stabilized final state of 4.5 without including the transition to
4.6.

If the current version has no matching entry, the versions are non-monotonic, or there is no later
transition, do not guess. Report that the plugin is up to date or that history is insufficient.

## Derive the implementation from protocol evidence

For every candidate in the selected span, read its complete `service.md` snapshot and
`service.patch`. Use `affected_rpcs` and `affected_types` as navigation aids, not as a substitute for
the definitions. Inspect all corresponding Java elements, consumers, request serialization,
response dispatch, unit tests, SDK-backed tests, and relevant Git history in the plugin.

Before editing, build an explicit mapping from each protocol change to the Java symbols and files it
requires. Derive names, inheritance, nullability, union handling, numeric widths, defaults, overloads,
and callback behavior from the specification and established neighboring implementations. A new
protocol type does not automatically imply a one-file change, and the audit intentionally supplies
no generated file delta.

Write the Java directly. Preserve intentional compatibility behavior and historical copyright years
in existing files; new files use the current calendar year. If the protocol is ambiguous or an
existing implementation conflicts with the likely translation, stop for a human decision rather
than recreating or guessing what a generator would have emitted.

The candidate span remains fixed through the test-first pauses. Because test and usage sources are
manifest inputs, regenerate and validate a fresh bundle after adding the accepted tests and before
production implementation. Confirm that the new report selects the same span; if the SDK or driver
inputs changed enough to alter it, tell the user and stop rather than silently changing scope.
