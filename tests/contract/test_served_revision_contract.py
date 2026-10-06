"""The revision watcher's fact vocabulary, pinned to traceability's transcription of it.

`services/reporting/traceability.py` joins a `container_image` release to the revision watcher's
reading of production serving its built commit. The watcher lives in a different program, which
`src/orchestrator` may not import, so a renamed key would empty that hop in silence. Tests may
import both sides, which makes this the place for the pin.
"""

from orchestrator.services.reporting import traceability
from revision_watcher import record
from revision_watcher.census import Reading
from revision_watcher.subjects import Subject


def test_the_transcribed_row_identity_matches_the_watcher() -> None:
    assert traceability.SERVED_SOURCE_SYSTEM == record.SOURCE_SYSTEM
    assert traceability.SERVED_SUBJECT_TYPE == record.SUBJECT_TYPE
    assert traceability.SERVED_OBSERVATION_TYPE == record.OBSERVATION_TYPE


def test_the_transcribed_fact_keys_read_back_what_the_watcher_writes() -> None:
    subject = Subject(
        name="orchestrator",
        health_url="https://sds.alobar.net/health/live",
        repository="AlobarQuest/orchestrator",
    )
    facts = record.revision_facts(Reading(subject=subject, state="current", served="b" * 40))

    assert facts[traceability.SERVED_REPOSITORY] == "AlobarQuest/orchestrator"
    assert facts[traceability.SERVED_COMMIT] == "b" * 40
