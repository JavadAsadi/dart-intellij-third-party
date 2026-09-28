from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import vm_service_audit as audit  # noqa: E402


def spec(version: str, rpc_body: str = "", type_body: str = "", note: str = "") -> bytes:
    return f"""# Dart VM Service Protocol {version}

## Public RPCs

{rpc_body}
## Public Types

{type_body}
## Revision History

version | comments
------- | --------
4.3 | Baseline.
{note}
""".encode()


def args(**overrides: object) -> argparse.Namespace:
    values = {
        "sdk": None,
        "service_md": None,
        "baseline_service_md": None,
        "github": False,
        "allow_network": False,
        "target_ref": "HEAD",
        "latest_known_sha": None,
        "allow_stale_sdk": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ParsingTest(unittest.TestCase):
    def test_versions_are_parsed_from_spec_and_java(self) -> None:
        self.assertEqual("4.22", audit.protocol_version(spec("4.22")))
        java = "public static final int versionMajor = 4;\nint versionMinor=22;"
        self.assertEqual("4.22", audit.plugin_protocol_version(java))

    def test_semantic_and_revision_deltas_preserve_document_order(self) -> None:
        before = spec(
            "4.3",
            "### alpha\nold\n### removed\nbye\n",
            "### TypeA\nold\n",
        )
        after = spec(
            "4.4",
            "### beta\nnew\n### alpha\nchanged\n",
            "### TypeA\nold\n### TypeB\nnew\n",
            "4.4 | Added beta.\n",
        )
        old_rpcs, old_types = audit.semantic_sections(before)
        new_rpcs, new_types = audit.semantic_sections(after)
        self.assertEqual(
            [
                {"name": "beta", "change": "added"},
                {"name": "alpha", "change": "changed"},
                {"name": "removed", "change": "removed"},
            ],
            audit.ordered_mapping_delta(old_rpcs, new_rpcs),
        )
        self.assertEqual(
            [{"name": "TypeB", "change": "added"}],
            audit.ordered_mapping_delta(old_types, new_types),
        )
        self.assertEqual(
            [{"version": "4.4", "comments": "Added beta."}],
            audit.revision_delta(before, after),
        )

class ReadinessTest(unittest.TestCase):
    def test_protocol_versions_determine_readiness_without_generated_code(self) -> None:
        versions = ["4.3", "4.4", "4.4", "4.5", "4.6"]
        self.assertEqual(
            "update_required",
            audit.readiness_for("4.5", "4.6", versions, True, []),
        )
        self.assertEqual(
            "ready_with_warning",
            audit.readiness_for("4.5", "4.6", versions, True, ["warning"]),
        )
        self.assertEqual(
            "incomplete_history",
            audit.readiness_for("4.5", "4.6", versions, False, []),
        )
        self.assertEqual(
            "up_to_date",
            audit.readiness_for("4.6", "4.6", versions, True, []),
        )
        self.assertEqual(
            "blocked",
            audit.readiness_for("4.2", "4.6", versions, True, []),
        )

    def test_non_monotonic_protocol_history_is_blocked(self) -> None:
        self.assertEqual(
            "blocked",
            audit.readiness_for(
                "4.4", "4.6", ["4.3", "4.5", "4.4", "4.6"], True, []
            ),
        )


class SourceSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()
        self.sdk = self.root / "sdk"
        self.sdk.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "VM Service Audit Test")
        self._git("config", "user.email", "vm-service-audit@example.invalid")
        self.service_path = self.sdk / "runtime/vm/service/service.md"
        self.service_path.parent.mkdir(parents=True)
        self.baseline_spec = spec("4.3")
        self.service_path.write_bytes(self.baseline_spec)
        self._git("add", "runtime/vm/service/service.md")
        self._git("commit", "-m", "baseline")
        self.baseline_sha = self._git("rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.sdk), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _metadata(self) -> dict[str, str]:
        return {
            "protocol_version": "4.3",
            "sdk_commit": self.baseline_sha,
            "service_md_path": "runtime/vm/service/service.md",
            "service_md_sha256": hashlib.sha256(self.baseline_spec).hexdigest(),
        }

    def _commit_spec(self, version: str, title: str) -> str:
        self.service_path.write_bytes(
            spec(
                version,
                f"### rpc{version.replace('.', '')}\nbody\n",
                note=f"{version} | {title}.\n",
            )
        )
        self._git("add", "runtime/vm/service/service.md")
        self._git("commit", "-m", title)
        return self._git("rev-parse", "HEAD")

    def test_local_checkout_reconstructs_ordered_history(self) -> None:
        first = self._commit_spec("4.4", "first")
        second = self._commit_spec("4.5", "second")
        result = audit.load_source(
            args(sdk=str(self.sdk), latest_known_sha=second),
            self.plugin,
            self._metadata(),
        )
        self.assertTrue(result.history_complete)
        self.assertEqual([first, second], [candidate.sha for candidate in result.candidates])
        self.assertEqual("current", result.checkout["remote_status"])
        self.assertEqual([], result.warnings)

    def test_stale_checkout_requires_explicit_human_choice(self) -> None:
        stale = self._commit_spec("4.4", "stale target")
        latest = self._commit_spec("4.5", "latest target")
        with self.assertRaisesRegex(audit.AuditError, "stale"):
            audit.load_source(
                args(
                    sdk=str(self.sdk),
                    target_ref=stale,
                    latest_known_sha=latest,
                ),
                self.plugin,
                self._metadata(),
            )
        result = audit.load_source(
            args(
                sdk=str(self.sdk),
                target_ref=stale,
                latest_known_sha=latest,
                allow_stale_sdk=True,
            ),
            self.plugin,
            self._metadata(),
        )
        self.assertEqual("stale", result.checkout["remote_status"])
        self.assertIn("stale_sdk", [warning.code for warning in result.warnings])

    def test_unfetched_live_remote_mismatch_is_conservatively_stale(self) -> None:
        self._commit_spec("4.4", "local target")
        unfetched_remote = "e" * 40
        with self.assertRaisesRegex(audit.AuditError, "stale"):
            audit.load_source(
                args(
                    sdk=str(self.sdk),
                    latest_known_sha=unfetched_remote,
                ),
                self.plugin,
                self._metadata(),
            )

    def test_standalone_spec_reports_incomplete_history(self) -> None:
        standalone_root = self.root / "isolated/standalone-plugin"
        standalone_root.mkdir(parents=True)
        target = self.root / "service.md"
        baseline = self.root / "baseline.md"
        target.write_bytes(spec("4.4", note="4.4 | Standalone.\n"))
        baseline.write_bytes(self.baseline_spec)
        result = audit.load_source(
            args(
                service_md=str(target),
                baseline_service_md=str(baseline),
            ),
            standalone_root,
            self._metadata(),
        )
        self.assertFalse(result.history_complete)
        self.assertIsNone(result.target_sha)
        self.assertEqual("incomplete_history", result.warnings[0].code)

    def test_github_fallback_uses_immutable_main_and_history(self) -> None:
        target_sha = "a" * 40
        target_spec = spec("4.4", note="4.4 | GitHub target.\n")
        baseline = self.root / "baseline.md"
        baseline.write_bytes(self.baseline_spec)
        history = [
            audit.CandidateState(
                sha=target_sha,
                title="GitHub target",
                spec=target_spec,
            )
        ]
        with (
            mock.patch.object(audit, "github_main_sha", return_value=target_sha),
            mock.patch.object(audit, "_github_raw", return_value=target_spec),
            mock.patch.object(audit, "github_history", return_value=history),
        ):
            result = audit.load_source(
                args(
                    github=True,
                    allow_network=True,
                    baseline_service_md=str(baseline),
                ),
                self.plugin,
                self._metadata(),
            )
        self.assertEqual("github", result.kind)
        self.assertEqual(target_sha, result.target_sha)
        self.assertTrue(result.history_complete)
        self.assertEqual(history, result.candidates)


class ReportGenerationTest(unittest.TestCase):
    def test_bundle_contains_protocol_evidence_without_generated_java(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "plugin"
            service_root = repo / audit.SERVICE_ROOT_REL
            service_root.mkdir(parents=True)
            (service_root / "VmService.java").write_text(
                "public static final int versionMajor = 4;\n"
                "public static final int versionMinor = 3;\n",
                encoding="utf-8",
            )
            baseline_spec = spec("4.3")
            target_spec = spec(
                "4.4",
                "### getFoo\nReturns a Foo.\n",
                "### Foo\nA response.\n",
                "4.4 | Added getFoo.\n",
            )
            target_sha = "a" * 40
            source = audit.SourceData(
                kind="sdk_checkout",
                target_spec=target_spec,
                target_sha=target_sha,
                baseline_spec=baseline_spec,
                candidates=[
                    audit.CandidateState(
                        sha=target_sha,
                        title="Add getFoo",
                        spec=target_spec,
                    )
                ],
                history_complete=True,
                latest_known_remote_sha=target_sha,
                latest_known_source="argument",
                checkout=None,
                warnings=[],
                input_labels={
                    "baseline_service_md": "fixture-baseline",
                    "target_service_md": "fixture-target",
                },
            )
            baseline = {
                "protocol_version": "4.3",
                "sdk_commit": "b" * 40,
                "service_md_path": "runtime/vm/service/service.md",
                "service_md_sha256": hashlib.sha256(baseline_spec).hexdigest(),
            }

            destination, report = audit.build_report(
                args(output=str(root / "reports")),
                repo,
                SKILL_ROOT,
                source,
                baseline,
            )

            self.assertEqual("2.0.0", report["schema_version"])
            self.assertEqual("update_required", report["readiness"])
            self.assertEqual(
                [{"name": "getFoo", "change": "added"}],
                report["change_candidates"][0]["affected_rpcs"],
            )
            self.assertEqual(
                {"spec_snapshot", "service_diff"},
                set(report["change_candidates"][0]["artifacts"]),
            )
            self.assertNotIn("drift", report)
            self.assertNotIn("required_work", report)
            self.assertFalse((destination / "generated").exists())
            manifest = json.loads(
                (destination / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(
                any("generator" in entry["id"] for entry in manifest["inputs"])
            )


class ContractValidationTest(unittest.TestCase):
    def test_schema_validator_rejects_unknown_keys_and_bad_patterns(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["sha"],
            "properties": {
                "sha": {"type": "string", "pattern": "^[0-9a-f]{4}$"}
            },
        }
        audit.validate_json_schema({"sha": "abcd"}, schema)
        with self.assertRaises(audit.AuditError):
            audit.validate_json_schema({"sha": "bad!"}, schema)
        with self.assertRaises(audit.AuditError):
            audit.validate_json_schema({"sha": "abcd", "extra": True}, schema)

    def test_bundle_validation_detects_artifact_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            report = {"value": "valid"}
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {"value": {"const": "valid"}},
            }
            artifact = bundle / "artifact.txt"
            artifact.write_text("original", encoding="utf-8")
            (bundle / "report.json").write_text(json.dumps(report), encoding="utf-8")
            (bundle / "report.schema.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            manifest = {
                "artifacts": [
                    {"path": "artifact.txt", "sha256": digest, "bytes": 8}
                ],
                "inputs": [
                    {
                        "id": "fixture",
                        "source": "fixture",
                        "artifact": "artifact.txt",
                        "sha256": digest,
                    }
                ],
            }
            (bundle / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            audit.validate_bundle(bundle)
            artifact.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(audit.AuditError, "hash mismatch"):
                audit.validate_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
