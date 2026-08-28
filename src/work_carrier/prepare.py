"""Turn one approved change record into a ready intake payload, or say why it cannot be.

**THIS MODULE STOPS AT A VERIFIED PAYLOAD, AND THAT BOUNDARY IS DELIBERATE.** It reads the
approved record, finds the package it names, and gets the payload built and VERIFIED. Whether
that payload is then printed or registered is `cli.py`'s decision and no business of this one --
so every refusal here is a refusal to produce a payload at all, which is the same refusal in
both modes. Before ADR-0027 this module carried the argument for why the carry could not
complete: intake required an `ActorRole.HUMAN` actor, so no scheduled program could register
one. That gate is gone, having been found to protect a transcription rather than a judgment, and
what replaced it is the change record id this module already refuses to omit.

**IT SHELLS OUT TO `orchestrator emit-intake-payload` RATHER THAN IMPORTING THE FUNCTION.** Two
reasons, and the second is the one that decided it. First, `work_carrier` is a separate program
(ADR-0002's shape) and every sibling -- the watcher, the ledger, the lander, the reconciliation
runner -- is held by an architecture test to importing nothing from `orchestrator`; a program
that imported it would be the precedent that makes the rule soft. Second, and better: the
documented intake payload is what that command emits, so a carry that runs it produces
BYTE-IDENTICAL output to what a person produces by hand and pastes into the form. That is the
strongest available statement that the carry relaxes nothing -- there is no second path to
diverge, because there is no second path.

**IT VERIFIES BY BUILDING, WHICH IS WHY THERE IS NO SEPARATE CHECK.** The emitter reads
`package.yaml` and `lineage.yaml`, requires `status == current_state == approved`, and requires
exactly one lineage approval whose hash equals `canonical_package_hash(package)`, which it
verifies against the tamper-evident chain. A package that is not genuinely approved makes the
command fail rather than emit. Nothing here re-implements those checks and nothing here can
weaken them: a refusal from the emitter is a refusal from the carry.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from work_carrier.change_manager import WorkRecord

EMITTER = "orchestrator"
EMIT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class Prepared:
    """A change record and the intake payload a human can paste for it."""

    record: WorkRecord
    package_path: Path
    payload: dict[str, Any]


@dataclass(frozen=True)
class Refused:
    """A change record the carry will not prepare, and why.

    NOTHING IS WRITTEN ANYWHERE when this is returned. The record keeps its status, its
    decision and its history; the orchestrator learns nothing; no partial payload exists. That
    is a property of the ORDER rather than of this branch: the registration `cli.py` may perform
    happens only on a `Prepared`, so a refusal is reached before anything could have been sent.
    change-manager is untouched either way -- this program holds no write path to it at all.
    """

    record: WorkRecord
    reason: str
    detail: str


def package_path(root: Path, record: WorkRecord) -> Path:
    """Where this machine keeps the checkout the record names.

    `package_source_repository` is `owner/repo`; the checkout is the repository's own name under
    the configured root, which is how every repository on this machine is already laid out. Only
    the last segment is used, so a record naming another owner's fork resolves to the same
    checkout -- which is why the payload the emitter returns is re-checked against the record
    below rather than the path being trusted to have found the right package.
    """
    return root / record.package_source_repository.split("/")[-1] / "packages" / record.package_id


def emit_key(record: WorkRecord) -> str:
    """The idempotency key handed to the emitter.

    Derived from the record so a re-run prints the same bytes rather than a payload that differs
    only in a random field, which would make two passes look like two different pieces of work.
    It IS the key a `--register` pass registers under -- `POST /api/v1/package-intakes` takes
    the payload's own key -- which is what makes a second scheduled pass over an unchanged queue
    a replay rather than a second intake. It is NOT the key a paste registers under:
    `/review/intakes` takes its key from the CSRF-bound form field and ignores the pasted one, so
    that re-submitting the rendered page is a replay for its own separate reason.
    """
    return f"work-carry-{record.change_record_id}-{record.package_revision}"


def prepare(
    record: WorkRecord, *, checkout_root: Path, runner=subprocess.run
) -> Prepared | Refused:
    """The whole carry for one record. Total: it returns a refusal rather than raising."""
    path = package_path(checkout_root, record)
    if not path.is_dir():
        return Refused(
            record,
            "package_not_on_disk",
            f"no package checkout at {path}; the carry reads a package, it cannot author one",
        )

    command = [
        EMITTER,
        "emit-intake-payload",
        str(path),
        "--source-repository",
        record.package_source_repository,
        "--idempotency-key",
        emit_key(record),
        "--change-record",
        str(record.change_record_id),
        "--json",
    ]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=EMIT_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return Refused(
            record,
            "emitter_not_on_path",
            f"{EMITTER!r} is not on PATH; the carry cannot build a payload without it",
        )
    except subprocess.SubprocessError as error:
        return Refused(record, "emitter_failed", f"{EMITTER} emit-intake-payload: {error}")

    if completed.returncode != 0:
        # Every approval requirement lives behind this command, so this one branch covers "the
        # package is not approved", "its hash does not match its lineage approval", "the approval
        # is not in the tamper-evident chain" and "there is more than one". Reporting them as one
        # refusal in the emitter's own words is deliberate: a carry that classified them would be
        # a second copy of rules it does not own.
        detail = (completed.stderr or completed.stdout or "").strip() or "no output"
        return Refused(record, "package_not_intakeable", detail[:500])

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        return Refused(record, "emitter_output_unreadable", f"payload is not JSON: {error}")
    if not isinstance(payload, dict):
        return Refused(record, "emitter_output_unreadable", "payload is not a JSON object")

    if payload.get("revision") != record.package_revision:
        return Refused(
            record,
            "revision_mismatch",
            f"the approved record names revision {record.package_revision} and the checkout "
            f"holds revision {payload.get('revision')}; the carry will not substitute one "
            "for the other",
        )
    if payload.get("package_id") != record.package_id:
        return Refused(
            record,
            "package_mismatch",
            f"the approved record names package {record.package_id!r} and the checkout holds "
            f"{payload.get('package_id')!r}",
        )
    if payload.get("change_record_id") != record.change_record_id:
        # The join is the point of the increment, so its absence is a refusal rather than a
        # payload a human pastes and nobody notices is unattributed.
        return Refused(
            record,
            "join_missing",
            "the prepared payload does not name the change record that caused it",
        )
    return Prepared(record=record, package_path=path, payload=payload)
