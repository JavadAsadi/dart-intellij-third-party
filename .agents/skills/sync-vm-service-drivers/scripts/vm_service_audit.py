#!/usr/bin/env python3
"""Deterministic VM Service protocol audit implementation."""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "2.0.0"
GENERATED_BY = "sync-vm-service-drivers/audit.py"
SDK_API = "https://api.github.com/repos/dart-lang/sdk"
SDK_RAW = "https://raw.githubusercontent.com/dart-lang/sdk"
DRIVER_ROOT_REL = Path(
    "third_party/src/main/java/com/jetbrains/lang/dart/ide/runner/server/"
    "vmService/vmServiceDrivers"
)
SERVICE_ROOT_REL = DRIVER_ROOT_REL / "service"
DEFAULT_REPORT_ROOT = Path("third_party/build/reports/vm-service-drivers")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
PROTOCOL_HEADER = re.compile(
    r"^#\s+Dart VM Service Protocol\s+(\d+\.\d+)\s*$", re.MULTILINE
)
REVISION_ROW = re.compile(r"^(\d+\.\d+)\s*\|\s*(.+?)\s*$")
HEADING_3 = re.compile(r"^###\s+(.+?)\s*$")


class AuditError(RuntimeError):
    """A deterministic input, configuration, or validation failure."""


@dataclasses.dataclass(frozen=True)
class WarningRecord:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclasses.dataclass(frozen=True)
class CandidateState:
    sha: str | None
    title: str
    spec: bytes


@dataclasses.dataclass
class SourceData:
    kind: str
    target_spec: bytes
    target_sha: str | None
    baseline_spec: bytes
    candidates: list[CandidateState]
    history_complete: bool
    latest_known_remote_sha: str | None
    latest_known_source: str
    checkout: dict[str, Any] | None
    warnings: list[WarningRecord]
    input_labels: dict[str, str]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def protocol_version(spec: bytes | str) -> str:
    text = spec.decode("utf-8") if isinstance(spec, bytes) else spec
    match = PROTOCOL_HEADER.search(text)
    if not match:
        raise AuditError("Unable to parse the VM Service protocol version")
    return match.group(1)


def plugin_protocol_version(vm_service: bytes | str) -> str:
    text = vm_service.decode("utf-8") if isinstance(vm_service, bytes) else vm_service
    major = re.search(r"versionMajor\s*=\s*(\d+)\s*;", text)
    minor = re.search(r"versionMinor\s*=\s*(\d+)\s*;", text)
    if not major or not minor:
        raise AuditError("Unable to parse versionMajor/versionMinor from VmService.java")
    return f"{major.group(1)}.{minor.group(1)}"


def revision_history(spec: bytes | str) -> OrderedDict[str, str]:
    text = spec.decode("utf-8") if isinstance(spec, bytes) else spec
    marker = "## Revision History"
    if marker not in text:
        return OrderedDict()
    rows: OrderedDict[str, str] = OrderedDict()
    for line in text.split(marker, 1)[1].splitlines():
        match = REVISION_ROW.match(line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def semantic_sections(spec: bytes | str) -> tuple[OrderedDict[str, str], OrderedDict[str, str]]:
    text = spec.decode("utf-8") if isinstance(spec, bytes) else spec
    rpcs = _sections_between(text, "## Public RPCs", "## Public Types")
    types = _sections_between(text, "## Public Types", "## Revision History")
    return rpcs, types


def _sections_between(text: str, start: str, end: str) -> OrderedDict[str, str]:
    if start not in text or end not in text:
        return OrderedDict()
    body = text.split(start, 1)[1].split(end, 1)[0]
    sections: OrderedDict[str, list[str]] = OrderedDict()
    current: str | None = None
    for line in body.splitlines():
        heading = HEADING_3.match(line)
        if heading:
            current = heading.group(1)
            sections[current] = [line]
        elif current is not None:
            sections[current].append(line)
    return OrderedDict((name, "\n".join(lines).rstrip() + "\n") for name, lines in sections.items())


def ordered_mapping_delta(
    before: Mapping[str, str], after: Mapping[str, str]
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for name in after:
        if name not in before:
            result.append({"name": name, "change": "added"})
        elif before[name] != after[name]:
            result.append({"name": name, "change": "changed"})
    for name in before:
        if name not in after:
            result.append({"name": name, "change": "removed"})
    return result


def revision_delta(before: bytes, after: bytes) -> list[dict[str, str]]:
    old = revision_history(before)
    new = revision_history(after)
    return [
        {"version": version, "comments": comments}
        for version, comments in new.items()
        if version not in old or old[version] != comments
    ]


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    text: bool = False,
) -> subprocess.CompletedProcess[Any]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "CI": "true",
        }
    )
    result = subprocess.run(
        list(command), cwd=cwd, env=environment, capture_output=True, text=text
    )
    if check and result.returncode:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise AuditError(f"Command failed ({' '.join(command)}):\n{stderr.strip()}")
    return result


def git_bytes(repo: Path, *arguments: str, check: bool = True) -> bytes:
    return _run(["git", "-C", str(repo), *arguments], check=check).stdout


def git_text(repo: Path, *arguments: str, check: bool = True) -> str:
    return _run(
        ["git", "-C", str(repo), *arguments], check=check, text=True
    ).stdout.strip()


def git_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
    ).returncode == 0


def _github_request(url: str) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dart-intellij-sync-vm-service-drivers",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise AuditError(f"Unable to retrieve {url}: {error}") from error


def _github_json(url: str) -> Any:
    try:
        return json.loads(_github_request(url))
    except json.JSONDecodeError as error:
        raise AuditError(f"GitHub returned invalid JSON for {url}") from error


def _github_raw(commit: str, path: str) -> bytes:
    return _github_request(f"{SDK_RAW}/{commit}/{path}")


def github_main_sha() -> str:
    payload = _github_json(f"{SDK_API}/git/ref/heads/main")
    sha = payload["object"]["sha"]
    if not HEX_40.fullmatch(sha):
        raise AuditError("GitHub returned an invalid SDK main SHA")
    return sha


def github_history(target_sha: str, baseline_sha: str, spec_path: str) -> list[CandidateState]:
    descending: list[tuple[str, str]] = []
    found_baseline = False
    for page in range(1, 11):
        query = urllib.parse.urlencode(
            {"path": spec_path, "sha": target_sha, "per_page": 100, "page": page}
        )
        payload = _github_json(f"{SDK_API}/commits?{query}")
        if not isinstance(payload, list):
            raise AuditError("GitHub commit history response was not a list")
        if not payload:
            break
        for entry in payload:
            sha = entry["sha"]
            if sha == baseline_sha:
                found_baseline = True
                break
            title = entry["commit"]["message"].splitlines()[0]
            descending.append((sha, title))
        if found_baseline:
            break
    if not found_baseline:
        raise AuditError("The baseline commit was not found in SDK service.md history")
    return [
        CandidateState(sha=sha, title=title, spec=_github_raw(sha, spec_path))
        for sha, title in reversed(descending)
    ]


def _remote_status(repo: Path, target: str, latest: str | None) -> str:
    if latest is None:
        return "unknown"
    if target == latest:
        return "current"
    # A live ls-remote SHA is often not present in a stale local object store.
    # Treat a mismatch conservatively as stale so the audit cannot silently
    # claim that an older clean main checkout is current.
    if _run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{latest}^{{commit}}"],
        check=False,
    ).returncode:
        return "stale"
    if git_is_ancestor(repo, target, latest):
        return "stale"
    if git_is_ancestor(repo, latest, target):
        return "ahead"
    return "diverged"


def _latest_remote_sha(
    sdk: Path, latest_known_sha: str | None, allow_network: bool
) -> tuple[str | None, str]:
    if latest_known_sha:
        if not HEX_40.fullmatch(latest_known_sha):
            raise AuditError("--latest-known-sha must be a full 40-character Git SHA")
        return latest_known_sha, "argument"
    if allow_network:
        remote = git_text(sdk, "remote", "get-url", "origin")
        output = _run(
            ["git", "ls-remote", remote, "refs/heads/main"], text=True
        ).stdout.strip()
        sha = output.split()[0] if output else ""
        if not HEX_40.fullmatch(sha):
            raise AuditError("Unable to resolve the SDK remote main SHA")
        return sha, "remote"
    cached = git_text(sdk, "rev-parse", "--verify", "refs/remotes/origin/main", check=False)
    return (cached, "remote_tracking") if HEX_40.fullmatch(cached) else (None, "unavailable")


def _read_baseline(
    baseline: Mapping[str, Any], sdk: Path | None, baseline_path: Path | None, allow_network: bool
) -> tuple[bytes, str]:
    if baseline_path:
        content = baseline_path.read_bytes()
        label = "provided-baseline-service-md"
    elif sdk:
        result = _run(
            [
                "git",
                "-C",
                str(sdk),
                "show",
                f"{baseline['sdk_commit']}:{baseline['service_md_path']}",
            ],
            check=False,
        )
        if result.returncode == 0:
            content = result.stdout
            label = f"sdk:{baseline['sdk_commit']}:{baseline['service_md_path']}"
        elif allow_network:
            content = _github_raw(baseline["sdk_commit"], baseline["service_md_path"])
            label = f"github:{baseline['sdk_commit']}:{baseline['service_md_path']}"
        else:
            raise AuditError(
                "The SDK checkout does not contain the baseline specification. "
                "Fetch history, provide --baseline-service-md, or rerun with --allow-network."
            )
    elif allow_network:
        content = _github_raw(baseline["sdk_commit"], baseline["service_md_path"])
        label = f"github:{baseline['sdk_commit']}:{baseline['service_md_path']}"
    else:
        raise AuditError(
            "A baseline specification requires an SDK checkout, --baseline-service-md, "
            "or --allow-network."
        )
    actual_hash = sha256_bytes(content)
    if actual_hash != baseline["service_md_sha256"]:
        raise AuditError(
            "Baseline service.md hash mismatch: "
            f"expected {baseline['service_md_sha256']}, got {actual_hash}"
        )
    if protocol_version(content) != baseline["protocol_version"]:
        raise AuditError("Baseline protocol version does not match baseline metadata")
    return content, label


def load_source(args: Any, repo_root: Path, baseline: Mapping[str, Any]) -> SourceData:
    sdk = Path(args.sdk).expanduser().resolve() if args.sdk else (repo_root.parent / "sdk")
    if not sdk.is_dir() or not (sdk / ".git").exists():
        sdk = None
    explicit_spec = Path(args.service_md).expanduser().resolve() if args.service_md else None
    baseline_path = (
        Path(args.baseline_service_md).expanduser().resolve()
        if args.baseline_service_md
        else None
    )

    if args.github:
        if explicit_spec or args.sdk:
            raise AuditError("--github cannot be combined with --sdk or --service-md")
        if not args.allow_network:
            raise AuditError("--github requires --allow-network")
        target_sha = github_main_sha()
        target_spec = _github_raw(target_sha, baseline["service_md_path"])
        baseline_spec, baseline_label = _read_baseline(baseline, None, baseline_path, True)
        warnings: list[WarningRecord] = []
        try:
            candidates = github_history(target_sha, baseline["sdk_commit"], baseline["service_md_path"])
            history_complete = True
        except AuditError as error:
            candidates = []
            history_complete = False
            warnings.append(WarningRecord("incomplete_history", str(error)))
        return SourceData(
            kind="github",
            target_spec=target_spec,
            target_sha=target_sha,
            baseline_spec=baseline_spec,
            candidates=candidates,
            history_complete=history_complete,
            latest_known_remote_sha=target_sha,
            latest_known_source="github_main",
            checkout=None,
            warnings=warnings,
            input_labels={
                "baseline_service_md": baseline_label,
                "target_service_md": f"github:{target_sha}:{baseline['service_md_path']}",
            },
        )

    if sdk is None:
        if not explicit_spec:
            raise AuditError(
                "No sibling ../sdk checkout was found. Provide --sdk, provide --service-md "
                "with a baseline source, or use --github --allow-network."
            )
        target_spec = explicit_spec.read_bytes()
        baseline_spec, baseline_label = _read_baseline(
            baseline, None, baseline_path, args.allow_network
        )
        warnings = [
            WarningRecord(
                "incomplete_history",
                "The standalone service.md could not be associated with SDK Git history.",
            )
        ]
        latest = github_main_sha() if args.allow_network else None
        return SourceData(
            kind="service_md",
            target_spec=target_spec,
            target_sha=None,
            baseline_spec=baseline_spec,
            candidates=[],
            history_complete=False,
            latest_known_remote_sha=latest,
            latest_known_source="github_main" if latest else "unavailable",
            checkout=None,
            warnings=warnings,
            input_labels={
                "baseline_service_md": baseline_label,
                "target_service_md": "provided-service-md",
            },
        )

    try:
        sdk_root = Path(git_text(sdk, "rev-parse", "--show-toplevel")).resolve()
    except AuditError as error:
        raise AuditError(f"Not a usable SDK Git checkout: {sdk}") from error
    target_sha = git_text(sdk_root, "rev-parse", f"{args.target_ref}^{{commit}}")
    head = git_text(sdk_root, "rev-parse", "HEAD")
    branch = git_text(sdk_root, "branch", "--show-current") or "detached"
    clean = not bool(git_text(sdk_root, "status", "--porcelain"))
    committed_target_spec = git_bytes(
        sdk_root, "show", f"{target_sha}:{baseline['service_md_path']}"
    )
    target_spec = explicit_spec.read_bytes() if explicit_spec else committed_target_spec
    baseline_spec, baseline_label = _read_baseline(
        baseline, sdk_root, baseline_path, args.allow_network
    )
    latest, latest_source = _latest_remote_sha(
        sdk_root, args.latest_known_sha, args.allow_network
    )
    status = _remote_status(sdk_root, target_sha, latest)
    if status == "stale" and not args.allow_stale_sdk:
        raise AuditError(
            "The selected SDK target is stale relative to latest-known main. Ask whether "
            "the clean main checkout may be fast-forwarded, or rerun with --allow-stale-sdk "
            "after the human chooses to audit the stale target."
        )

    warnings: list[WarningRecord] = []
    if latest_source == "remote_tracking":
        warnings.append(
            WarningRecord(
                "remote_not_verified",
                "SDK freshness was checked against the cached origin/main ref, not the live remote.",
            )
        )
    if latest is None:
        warnings.append(
            WarningRecord(
                "remote_status_unknown",
                "No live or cached SDK main SHA was available for freshness verification.",
            )
        )
    if status == "stale":
        warnings.append(
            WarningRecord(
                "stale_sdk",
                f"The selected SDK target {target_sha[:12]} is behind latest-known main {latest[:12]}.",
            )
        )
    elif status in {"ahead", "diverged"}:
        warnings.append(
            WarningRecord(
                f"sdk_{status}",
                f"The selected SDK target is {status} relative to latest-known main.",
            )
        )
    if branch != "main":
        warnings.append(
            WarningRecord(
                "non_main_checkout",
                f"The SDK checkout is on {branch!r}; it was inspected without modification.",
            )
        )
    if not clean:
        warnings.append(
            WarningRecord(
                "dirty_sdk_checkout",
                "The SDK checkout is dirty; the audit used the immutable selected commit.",
            )
        )

    history_complete = False
    candidates: list[CandidateState] = []
    spec_matches_commit = target_spec == committed_target_spec
    if spec_matches_commit and git_is_ancestor(sdk_root, baseline["sdk_commit"], target_sha):
        shas = git_text(
            sdk_root,
            "rev-list",
            "--reverse",
            f"{baseline['sdk_commit']}..{target_sha}",
            "--",
            baseline["service_md_path"],
        ).splitlines()
        candidates = [
            CandidateState(
                sha=sha,
                title=git_text(sdk_root, "show", "-s", "--format=%s", sha),
                spec=git_bytes(sdk_root, "show", f"{sha}:{baseline['service_md_path']}"),
            )
            for sha in shas
            if sha
        ]
        history_complete = True
    else:
        reason = (
            "The provided service.md does not match the selected SDK commit."
            if not spec_matches_commit
            else "The recorded plugin baseline is not an ancestor of the selected SDK commit."
        )
        warnings.append(WarningRecord("incomplete_history", reason))

    try:
        relative_sdk = os.path.relpath(sdk_root, repo_root)
    except ValueError:
        relative_sdk = "external-sdk"
    return SourceData(
        kind="service_md" if explicit_spec else "sdk_checkout",
        target_spec=target_spec,
        target_sha=target_sha if spec_matches_commit else None,
        baseline_spec=baseline_spec,
        candidates=candidates,
        history_complete=history_complete,
        latest_known_remote_sha=latest,
        latest_known_source=latest_source,
        checkout={
            "path": Path(relative_sdk).as_posix(),
            "head": head,
            "branch": branch,
            "clean": clean,
            "remote_status": status,
        },
        warnings=warnings,
        input_labels={
            "baseline_service_md": baseline_label,
            "target_service_md": (
                "provided-service-md"
                if explicit_spec
                else f"sdk:{target_sha}:{baseline['service_md_path']}"
            ),
        },
    )


def unified_text_diff(before: bytes, after: bytes, path: str) -> str:
    before_lines = before.decode("utf-8").splitlines(keepends=True)
    after_lines = after.decode("utf-8").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{path}" if before else "/dev/null",
            tofile=f"b/{path}" if after else "/dev/null",
            lineterm="\n",
        )
    )


def collect_search_files(repo_root: Path) -> tuple[list[tuple[str, list[str]]], list[tuple[str, list[str]]]]:
    def collect(root: Path, exclude_drivers: bool) -> list[tuple[str, list[str]]]:
        if not root.is_dir():
            return []
        result: list[tuple[str, list[str]]] = []
        driver_root = (repo_root / DRIVER_ROOT_REL).resolve()
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".java", ".kt"} or not path.is_file():
                continue
            resolved = path.resolve()
            if exclude_drivers and (resolved == driver_root or driver_root in resolved.parents):
                continue
            result.append(
                (
                    path.relative_to(repo_root).as_posix(),
                    path.read_text("utf-8", errors="replace").splitlines(),
                )
            )
        return result

    return (
        collect(repo_root / "third_party/src/main", True),
        collect(repo_root / "third_party/src/test", False),
    )


def find_sites(files: Sequence[tuple[str, list[str]]], symbols: Iterable[str]) -> list[dict[str, Any]]:
    useful = sorted(
        {
            symbol
            for symbol in symbols
            if symbol and re.match(r"^[A-Za-z_$][\w$]*$", symbol)
        }
    )
    if not useful:
        return []
    pattern = re.compile(
        r"(?<![A-Za-z0-9_$])("
        + "|".join(map(re.escape, useful))
        + r")(?![A-Za-z0-9_$])"
    )
    sites: list[dict[str, Any]] = []
    for path, lines in files:
        for line_number, line in enumerate(lines, 1):
            matches = sorted(set(pattern.findall(line)))
            if matches:
                sites.append({"path": path, "line": line_number, "symbols": matches})
    return sites


def candidate_symbols(
    affected_rpcs: Sequence[Mapping[str, str]],
    affected_types: Sequence[Mapping[str, str]],
) -> set[str]:
    symbols = {entry["name"] for entry in affected_rpcs}
    symbols.update(entry["name"] for entry in affected_types)
    return symbols


def all_file_hashes(root: Path, repo_root: Path) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(repo_root).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def readiness_for(
    plugin_version: str,
    target_version: str,
    known_versions: Sequence[str],
    history_complete: bool,
    warnings: Sequence[Any],
) -> str:
    if plugin_version == target_version:
        return "ready_with_warning" if warnings else "up_to_date"
    if not history_complete:
        return "incomplete_history"

    def version_key(version: str) -> tuple[int, int]:
        major, minor = version.split(".", 1)
        return int(major), int(minor)

    version_keys = [version_key(version) for version in known_versions]
    if any(before > after for before, after in zip(version_keys, version_keys[1:])):
        return "blocked"
    if plugin_version not in known_versions or target_version not in known_versions:
        return "blocked"
    if version_key(plugin_version) >= version_key(target_version):
        return "blocked"
    if warnings:
        return "ready_with_warning"
    return "update_required"


def markdown_report(report: Mapping[str, Any]) -> str:
    source = report["source"]
    plugin = report["plugin"]
    target = source["target"]
    lines = [
        "# VM Service Protocol Synchronization Report",
        "",
        f"- Plugin protocol: **{plugin['protocol_version']}**",
        f"- Historical audit baseline: **{plugin['baseline']['protocol_version']}**",
        f"- SDK target protocol: **{target['protocol_version']}**",
        f"- SDK target commit: `{target['sdk_commit'] or 'unidentified standalone specification'}`",
        f"- Readiness: **{report['readiness']}**",
        "",
    ]
    if source["warnings"]:
        lines.extend(["## Warnings", ""])
        for warning in source["warnings"]:
            lines.append(f"> **{warning['code']}** — {warning['message']}")
            lines.append(">")
        if lines[-1] == ">":
            lines.pop()
        lines.append("")
    lines.extend(["## SDK revision notes", ""])
    notes = report["semantic_changes"]
    if notes:
        lines.extend(["| Version | Change |", "| --- | --- |"])
        for note in notes:
            lines.append(
                f"| {note['version']} | {note['comments'].replace('|', '&#124;')} |"
            )
    else:
        lines.append("No revision-history changes relative to the recorded baseline.")
    lines.extend(["", "## Ordered change candidates", ""])
    candidates = report["change_candidates"]
    if candidates:
        lines.extend(
            [
                "| # | SDK commit | Protocol | RPCs | Types | Change |",
                "| ---: | --- | --- | ---: | ---: | --- |",
            ]
        )
        for candidate in candidates:
            short_sha = (
                candidate["sdk_commit"][:12]
                if candidate["sdk_commit"]
                else "unidentified"
            )
            title = candidate["title"].replace("|", "&#124;")
            lines.append(
                f"| {candidate['order']} | `{short_sha}` | "
                f"{candidate['protocol_before']} → {candidate['protocol_after']} | "
                f"{len(candidate['affected_rpcs'])} | "
                f"{len(candidate['affected_types'])} | {title} |"
            )
    else:
        lines.append("No SDK specification commits occurred after the recorded baseline.")
    for candidate in candidates:
        rpc_changes = candidate["affected_rpcs"]
        type_changes = candidate["affected_types"]
        if not rpc_changes and not type_changes:
            continue
        lines.extend(["", f"### Candidate {candidate['order']}", ""])
        lines.append(
            "- RPCs: "
            + (
                ", ".join(
                    f"{entry['change']} `{entry['name']}`" for entry in rpc_changes
                )
                or "None."
            )
        )
        lines.append(
            "- Types: "
            + (
                ", ".join(
                    f"{entry['change']} `{entry['name']}`" for entry in type_changes
                )
                or "None."
            )
        )
    lines.extend(
        [
            "",
            "## Updater handoff",
            "",
            "`report.json` is authoritative. Validate it against `report.schema.json` and verify "
            "`manifest.json` plus every plugin precondition hash before making changes. Treat each "
            "SDK commit above as an ordered candidate; combine related candidates into a meaningful "
            "feature only after human review. This bundle contains no generated Java: derive and "
            "write the implementation from the selected specification snapshots and the plugin's "
            "existing conventions.",
            "",
        ]
    )
    return "\n".join(lines)


def _safe_replace_directory(
    staging: Path, destination: Path, report_root: Path
) -> None:
    report_root_resolved = report_root.resolve()
    destination_resolved = destination.resolve()
    if report_root_resolved not in destination_resolved.parents:
        raise AuditError(f"Refusing to replace report directory outside {report_root}")
    if destination.exists():
        shutil.rmtree(destination)
    staging.replace(destination)


def build_report(
    args: Any,
    repo_root: Path,
    skill_root: Path,
    source: SourceData,
    baseline: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    target_version = protocol_version(source.target_spec)
    baseline_version = protocol_version(source.baseline_spec)
    if baseline_version != baseline["protocol_version"]:
        raise AuditError("Specification baseline version does not match recorded metadata")

    candidate_states = list(source.candidates)
    if not candidate_states or candidate_states[-1].spec != source.target_spec:
        if source.target_spec != source.baseline_spec:
            candidate_states.append(
                CandidateState(
                    sha=source.target_sha,
                    title="Net specification change (history unavailable)",
                    spec=source.target_spec,
                )
            )
    plugin_vm_service = repo_root / SERVICE_ROOT_REL / "VmService.java"
    if not plugin_vm_service.is_file():
        raise AuditError(f"VM Service driver does not exist: {plugin_vm_service}")
    plugin_version = plugin_protocol_version(plugin_vm_service.read_bytes())

    target_identity = source.target_sha or sha256_bytes(source.target_spec)
    source_id = f"{target_version}-{target_identity[:12]}"
    report_root = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (repo_root / DEFAULT_REPORT_ROOT)
    )
    report_root.mkdir(parents=True, exist_ok=True)
    destination = report_root / source_id
    staging = report_root / f".{source_id}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    (staging / "specs").mkdir()
    (staging / "specs/baseline-service.md").write_bytes(source.baseline_spec)
    (staging / "specs/target-service.md").write_bytes(source.target_spec)

    main_files, test_files = collect_search_files(repo_root)
    candidates_json: list[dict[str, Any]] = []
    previous_spec = source.baseline_spec
    for index, candidate in enumerate(candidate_states, 1):
        before_rpcs, before_types = semantic_sections(previous_spec)
        after_rpcs, after_types = semantic_sections(candidate.spec)
        affected_rpcs = ordered_mapping_delta(before_rpcs, after_rpcs)
        affected_types = ordered_mapping_delta(before_types, after_types)
        symbols = candidate_symbols(affected_rpcs, affected_types)
        artifact_dir = (
            Path("candidates")
            / f"{index:04d}-{(candidate.sha or sha256_bytes(candidate.spec))[:12]}"
        )
        (staging / artifact_dir).mkdir(parents=True)
        service_diff_path = artifact_dir / "service.patch"
        spec_snapshot_path = artifact_dir / "service.md"
        (staging / spec_snapshot_path).write_bytes(candidate.spec)
        (staging / service_diff_path).write_text(
            unified_text_diff(
                previous_spec, candidate.spec, "runtime/vm/service/service.md"
            ),
            encoding="utf-8",
        )
        candidates_json.append(
            {
                "order": index,
                "sdk_commit": candidate.sha,
                "title": candidate.title,
                "protocol_before": protocol_version(previous_spec),
                "protocol_after": protocol_version(candidate.spec),
                "revision_notes": revision_delta(previous_spec, candidate.spec),
                "affected_rpcs": affected_rpcs,
                "affected_types": affected_types,
                "plugin_usage_sites": find_sites(main_files, symbols),
                "existing_test_sites": find_sites(test_files, symbols),
                "artifacts": {
                    "spec_snapshot": spec_snapshot_path.as_posix(),
                    "service_diff": service_diff_path.as_posix(),
                },
            }
        )
        previous_spec = candidate.spec

    schema_source = skill_root / "references/report.schema.json"
    shutil.copyfile(schema_source, staging / "report.schema.json")
    driver_file_hashes = all_file_hashes(repo_root / DRIVER_ROOT_REL, repo_root)
    warnings_json = [warning.as_dict() for warning in source.warnings]
    known_versions = [baseline_version] + [
        protocol_version(candidate.spec) for candidate in candidate_states
    ]
    readiness = readiness_for(
        plugin_version,
        target_version,
        known_versions,
        source.history_complete,
        warnings_json,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "readiness": readiness,
        "source": {
            "kind": source.kind,
            "target": {
                "protocol_version": target_version,
                "sdk_commit": source.target_sha,
                "service_sha256": sha256_bytes(source.target_spec),
            },
            "latest_known_remote_sha": source.latest_known_remote_sha,
            "latest_known_source": source.latest_known_source,
            "checkout": source.checkout,
            "warnings": warnings_json,
            "history_complete": source.history_complete,
        },
        "plugin": {
            "protocol_version": plugin_version,
            "driver_root": DRIVER_ROOT_REL.as_posix(),
            "baseline": {
                key: baseline[key]
                for key in (
                    "protocol_version",
                    "sdk_commit",
                    "service_md_sha256",
                )
            },
            "files": driver_file_hashes,
        },
        "semantic_changes": revision_delta(
            source.baseline_spec, source.target_spec
        ),
        "change_candidates": candidates_json,
        "preconditions": {
            "plugin_files": {
                entry["path"]: entry["sha256"] for entry in driver_file_hashes
            },
            "baseline_service_sha256": sha256_bytes(source.baseline_spec),
            "target_service_sha256": sha256_bytes(source.target_spec),
        },
        "artifacts": {
            "report_markdown": "report.md",
            "report_json": "report.json",
            "schema": "report.schema.json",
            "manifest": "manifest.json",
            "baseline_spec": "specs/baseline-service.md",
            "target_spec": "specs/target-service.md",
        },
    }
    (staging / "report.json").write_bytes(json_bytes(report))
    (staging / "report.md").write_text(markdown_report(report), encoding="utf-8")

    input_entries = [
        {
            "id": "baseline_service_md",
            "source": source.input_labels["baseline_service_md"],
            "artifact": "specs/baseline-service.md",
            "sha256": sha256_bytes(source.baseline_spec),
        },
        {
            "id": "target_service_md",
            "source": source.input_labels["target_service_md"],
            "artifact": "specs/target-service.md",
            "sha256": sha256_bytes(source.target_spec),
        },
    ]
    for relative in (
        "scripts/audit.py",
        "scripts/vm_service_audit.py",
        "references/baseline.json",
        "references/report.schema.json",
    ):
        input_entries.append(
            {
                "id": f"skill/{relative}",
                "source": f"skill:{relative}",
                "sha256": sha256_file(skill_root / relative),
            }
        )
    for candidate, candidate_json in zip(candidate_states, candidates_json):
        input_entries.append(
            {
                "id": f"sdk_candidate/{candidate_json['order']:04d}",
                "source": (
                    f"sdk:{candidate.sha}:{baseline['service_md_path']}"
                    if candidate.sha
                    else "unidentified-standalone-specification"
                ),
                "artifact": candidate_json["artifacts"]["spec_snapshot"],
                "sha256": sha256_bytes(candidate.spec),
            }
        )
    input_entries.extend(
        {
            "id": f"plugin/{entry['path']}",
            "source": entry["path"],
            "sha256": entry["sha256"],
        }
        for entry in driver_file_hashes
    )
    searched_paths = sorted(
        {path for path, _ in main_files} | {path for path, _ in test_files}
    )
    input_entries.extend(
        {
            "id": f"usage_source/{relative}",
            "source": relative,
            "sha256": sha256_file(repo_root / relative),
        }
        for relative in searched_paths
    )
    input_entries.sort(key=lambda entry: entry["id"])
    artifact_entries = [
        {
            "path": path.relative_to(staging).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(staging.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    manifest = {
        "schema_version": "1.0.0",
        "hash_algorithm": "sha256",
        "note": "manifest.json is excluded because a file cannot contain its own stable hash.",
        "inputs": input_entries,
        "artifacts": artifact_entries,
    }
    (staging / "manifest.json").write_bytes(json_bytes(manifest))
    validate_bundle(staging, repo_root=repo_root, skill_root=skill_root)
    _safe_replace_directory(staging, destination, report_root)
    return destination, report


def validate_json_schema(
    instance: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any] | None = None,
    path: str = "$",
) -> None:
    root = root or schema
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/"):
            raise AuditError(f"Unsupported schema reference at {path}: {reference}")
        target: Any = root
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        validate_json_schema(instance, target, root, path)
        return
    if "const" in schema and instance != schema["const"]:
        raise AuditError(
            f"Schema validation failed at {path}: expected {schema['const']!r}"
        )
    if "enum" in schema and instance not in schema["enum"]:
        raise AuditError(
            f"Schema validation failed at {path}: {instance!r} is not allowed"
        )
    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        type_checks = {
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
            "string": lambda value: isinstance(value, str),
            "integer": lambda value: isinstance(value, int)
            and not isinstance(value, bool),
            "boolean": lambda value: isinstance(value, bool),
            "null": lambda value: value is None,
        }
        if not any(type_checks[name](instance) for name in options):
            raise AuditError(
                f"Schema validation failed at {path}: expected {options}"
            )
    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise AuditError(
                f"Schema validation failed at {path}: missing {missing}"
            )
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_schema = properties.get(key)
            if child_schema is None:
                if additional is False:
                    raise AuditError(
                        f"Schema validation failed at {path}: unexpected key {key}"
                    )
                if isinstance(additional, dict):
                    child_schema = additional
            if child_schema is not None:
                validate_json_schema(value, child_schema, root, f"{path}.{key}")
        if "propertyNames" in schema:
            for key in instance:
                validate_json_schema(
                    key, schema["propertyNames"], root, f"{path}.<key>"
                )
    if isinstance(instance, list):
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in instance]
            if len(serialized) != len(set(serialized)):
                raise AuditError(
                    f"Schema validation failed at {path}: duplicate array items"
                )
        if "items" in schema:
            for index, value in enumerate(instance):
                validate_json_schema(
                    value, schema["items"], root, f"{path}[{index}]"
                )
    if isinstance(instance, str) and "pattern" in schema:
        if not re.search(schema["pattern"], instance):
            raise AuditError(
                f"Schema validation failed at {path}: string does not match pattern"
            )
    if (
        isinstance(instance, int)
        and "minimum" in schema
        and instance < schema["minimum"]
    ):
        raise AuditError(f"Schema validation failed at {path}: below minimum")


def validate_bundle(
    bundle: Path,
    *,
    repo_root: Path | None = None,
    skill_root: Path | None = None,
) -> None:
    report = json.loads((bundle / "report.json").read_text("utf-8"))
    schema = json.loads((bundle / "report.schema.json").read_text("utf-8"))
    validate_json_schema(report, schema)
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    for entry in manifest["artifacts"]:
        path = bundle / entry["path"]
        if not path.is_file():
            raise AuditError(f"Manifest artifact is missing: {entry['path']}")
        if (
            path.stat().st_size != entry["bytes"]
            or sha256_file(path) != entry["sha256"]
        ):
            raise AuditError(f"Manifest artifact hash mismatch: {entry['path']}")
    for entry in manifest["inputs"]:
        identifier = entry["id"]
        if "artifact" in entry:
            path = bundle / entry["artifact"]
        elif identifier.startswith("skill/") and skill_root:
            path = skill_root / identifier.removeprefix("skill/")
        elif identifier.startswith("plugin/") and repo_root:
            path = repo_root / identifier.removeprefix("plugin/")
        elif identifier.startswith("usage_source/") and repo_root:
            path = repo_root / identifier.removeprefix("usage_source/")
        else:
            raise AuditError(
                f"Cannot resolve manifest input {identifier}; validate from the plugin "
                "repository with the skill available."
            )
        if not path.is_file():
            raise AuditError(f"Manifest input is missing: {identifier}")
        if sha256_file(path) != entry["sha256"]:
            raise AuditError(f"Manifest input hash mismatch: {identifier}")


def load_baseline(skill_root: Path) -> dict[str, Any]:
    baseline = json.loads(
        (skill_root / "references/baseline.json").read_text("utf-8")
    )
    required = {
        "protocol_version",
        "sdk_commit",
        "service_md_path",
        "service_md_sha256",
    }
    if set(baseline) != required:
        raise AuditError("baseline.json has unexpected or missing fields")
    if not HEX_40.fullmatch(baseline["sdk_commit"]):
        raise AuditError("baseline.json contains an invalid SDK commit")
    if not HEX_64.fullmatch(baseline["service_md_sha256"]):
        raise AuditError("baseline.json contains an invalid service.md hash")
    return baseline
