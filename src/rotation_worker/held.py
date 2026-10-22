"""A copy of a credential that an application holds in its settings to call another service.

ADR-0055 amendment 13. The orchestrator calls change-manager with change-manager's read bearer,
which it holds in `ORCHESTRATOR_CHANGE_RECORD_TOKEN`. A rotation of that bearer must write the copy
and confirm the running orchestrator loaded it: probing change-manager can't show that, because
change-manager accepts the new value whatever the orchestrator holds.

* **Distribute writes it** through Coolify, both rows, and restarts the application, under the
  orchestrator adapter's rules: never while a restart would strand a dispatched run, and only the
  configured orchestrator application.
* **Verify-consumers asks the holding application** for the fingerprint it loaded, on the
  orchestrator's executor-only `held-credentials` route. Only the orchestrator reports, so a
  destination in any other application is refused.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from rotation_worker.contract import ContractError
from rotation_worker.coolify import CoolifyClient
from rotation_worker.live_runs import stranded_by_a_restart
from rotation_worker.orchestrator_client import OrchestratorClient, OrchestratorError
from rotation_worker.values import Fingerprint, Value

INSTANCE: Final = "prod"
RESTART_WAIT: Final = timedelta(minutes=10)
RECHECK_SECONDS: Final = 10.0


class HeldSettings:
    """Writes and reads the orchestrator's copies of other services' credentials."""

    def __init__(
        self,
        orchestrator: OrchestratorClient,
        coolify: Callable[[str], CoolifyClient],
        application: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._orchestrator = orchestrator
        self._coolify = coolify
        self._application = application
        self._clock = clock
        self._sleep = sleep

    def check(self, consumer: Mapping[str, Any]) -> None:
        """Refuse a held destination this executor can't write or read back.

        A variable the orchestrator doesn't report could be written but never confirmed, so it is
        refused here, before the claim, not after a restart and a ten-minute wait.
        """
        if consumer["instance"] != INSTANCE or consumer["resource_type"] != "application":
            raise ContractError("a held copy is written only in a production application")
        if consumer["uuid"] != self._application:
            raise ContractError("only the orchestrator reports what it holds; refused elsewhere")
        if str(consumer["key"]) not in self._orchestrator.held():
            raise ContractError(f"the orchestrator doesn't report {consumer['key']}")

    def write(self, consumer: Mapping[str, Any], value: Value) -> None:
        self.check(consumer)
        if self.holds(consumer, value):
            return
        # Asked before writing and again straight before the restart, as the orchestrator adapter
        # does: the window can be narrowed, not closed.
        self._refuse_a_stranding_restart()
        coolify = self._coolify(self._application)
        coolify.set_variable(str(consumer["key"]), value.reveal())
        self._refuse_a_stranding_restart()
        coolify.restart()
        deadline = self._clock() + RESTART_WAIT
        while self._clock() < deadline:
            self._sleep(RECHECK_SECONDS)
            if self.holds(consumer, value):
                return
        raise ContractError("the restarted orchestrator didn't load the held copy in time")

    def holds(self, consumer: Mapping[str, Any], value: Value) -> bool:
        """Whether the running orchestrator loaded `value` for the setting. Never raises."""
        try:
            loaded = self._orchestrator.held().get(str(consumer["key"]))
        except OrchestratorError:
            # Mid-restart, or unreachable: not shown to hold it.
            return False
        if not isinstance(loaded, Mapping):
            return False
        prefix, length = loaded.get("sha256_prefix"), loaded.get("length")
        if not isinstance(prefix, str) or not isinstance(length, int):
            return False
        return value.fingerprint.matches(Fingerprint(prefix, length))

    def _refuse_a_stranding_restart(self) -> None:
        stranded = stranded_by_a_restart(self._orchestrator)
        if stranded:
            raise ContractError("a restart now would strand a run: " + ", ".join(stranded))
