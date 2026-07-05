from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_dependencies import register_unit


def test_two_workers_cannot_claim_same_unit(migrated_engine) -> None:
    with Session(migrated_engine) as setup:
        unit = register_unit(setup, "concurrent-claim")
        unit.state = "ready"
        setup.commit()
        unit_id = unit.id

    barrier = Barrier(2)

    def acquire(worker_id: str) -> LeaseGrant | DomainError:
        with Session(migrated_engine) as session:
            barrier.wait(timeout=5)
            return claim_unit(
                session,
                unit_id,
                ActorContext(worker_id, ActorRole.WORKER),
                f"claim-{worker_id}",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, ["worker-a", "worker-b"]))

    grants = [result for result in results if isinstance(result, LeaseGrant)]
    conflicts = [result for result in results if isinstance(result, DomainError)]
    assert len(grants) == 1
    assert [error.code for error in conflicts] == ["claim_conflict"]
