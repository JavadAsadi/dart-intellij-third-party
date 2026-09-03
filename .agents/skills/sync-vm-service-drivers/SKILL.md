---
name: sync-vm-service-drivers
description: Audit the Dart IntelliJ plugin's generated VM Service Java drivers against a Dart SDK specification and prepare a deterministic handoff bundle for a reviewed synchronization. Use for VM Service protocol drift, version checks, or planning driver updates; the current implementation reports changes but does not update driver sources.
---

# Sync VM Service Drivers

Audit first. Run the deterministic script from the repository root:

```bash
python3 .agents/skills/sync-vm-service-drivers/scripts/audit.py
```

The script prefers the sibling `../sdk` checkout, generates canonical Java for the recorded plugin baseline and the selected SDK target, and writes an ignored bundle under `third_party/build/reports/vm-service-drivers/`. A protocol mismatch is a successful audit result: inspect `report.md` and use `report.json` as the authoritative machine-readable handoff.

Validate a bundle before handing it to another agent or starting update work:

```bash
python3 .agents/skills/sync-vm-service-drivers/scripts/validate_report.py \
  third_party/build/reports/vm-service-drivers/<source-id>
```

## Source selection

- Before auditing, compare a sibling SDK checkout with remote `main` using read-only Git commands.
- If clean `main` is stale, ask whether the agent may fast-forward it or whether the human will update it. Never update it without approval.
- Never alter a dirty, diverged, or non-`main` SDK checkout automatically. Audit an immutable commit and call out the checkout status prominently.
- If the sibling checkout is unavailable, offer to clone a filtered SDK checkout. If that is declined, use the script's GitHub mode after obtaining permission for network access.
- For an explicit `service.md`, pass `--service-md`; also provide `--sdk` when possible so the report can reconstruct change provenance. Without history, the report is still usable but is marked `incomplete_history`.
- Use `--help` for source, network, Dart executable, and output options.

The generator bootstrap tries pinned dependencies offline first. It accesses pub.dev only when `--allow-network` is explicitly supplied. SDK GitHub fallback is likewise opt-in.

## Boundaries

- This phase must not edit anything under `vmServiceDrivers`.
- Treat SDK commits as ordered change candidates, not pre-approved features.
- Preserve plugin-owned files and local changes to generated files. An overlap with a target generated change is a blocker for a future updater, never permission to overwrite it.
- Do not use a report if its schema, manifest, target identity, or plugin precondition hashes no longer validate.

When implementing synchronization from a report, first read [the updater contract](references/updater-contract.md). That mode is documented for handoff compatibility but is not implemented by this version of the skill.
