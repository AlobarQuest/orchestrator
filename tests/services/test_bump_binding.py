"""The binding between a standing bump's envelope and the bump its package names (ADR-0028 am. 1).

Every passing envelope here is one intent-packages' generator emits: the uv pair from the Task 0
replay (records 170 and 171), and pip and npm from `profiles/dependency_update.py`.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from orchestrator.services.intake.bump_binding import bump_binding_violations

REPOSITORY = "AlobarQuest/orchestrator"


def _fields(package: str, old: str, new: str, repository: str = REPOSITORY) -> dict[str, Any]:
    return {
        "target_repo": repository,
        "package": package,
        "from_version": old,
        "to_version": new,
        "standing": True,
    }


def _envelope(
    mutations: list[str], verifiers: list[str], repository: str = REPOSITORY
) -> dict[str, Any]:
    return {
        "constraints": {
            "allowed_commands": [*mutations, *verifiers],
            "mutation_commands": list(mutations),
            "verify_commands": list(verifiers),
            "target_repository": repository,
        }
    }


UV_CHECK = ["uv lock --check"]
HTTPX2 = _fields("httpx2", "2.13.1", "2.16.0")
SQLALCHEMY = _fields("sqlalchemy", "2.0.54", "2.1.6")
UV_ID = "orchestrator-uv-httpx2"
PIP = _fields("flask", "3.0.0", "3.0.1")
PIP_ID = "orchestrator-pip-flask"
PIP_SED = "sed -i 's/^flask==3.0.0$/flask==3.0.1/' requirements.txt"
PIP_GREP = "grep -qx 'flask==3.0.1' requirements.txt"
NPM = _fields("@types/node", "22.1.0", "22.2.0")
NPM_ID = "orchestrator-npm-types-node"
NPM_INSTALL = "npm install @types/node@22.2.0 --save-exact"
NPM_GREP = 'grep -q \'"@types/node": "22.2.0"\' package.json'


@pytest.mark.parametrize(
    ("envelope", "fields", "package_id"),
    [
        pytest.param(
            _envelope(["uv add --dev 'httpx2>=2.16.0'"], UV_CHECK), HTTPX2, UV_ID, id="uv-dev"
        ),
        pytest.param(
            _envelope(["uv add 'sqlalchemy>=2.1.6'"], UV_CHECK),
            SQLALCHEMY,
            "orchestrator-uv-sqlalchemy",
            id="uv-main",
        ),
        pytest.param(
            _envelope(["uv add --group lint 'httpx2>=2.16.0'"], UV_CHECK),
            HTTPX2,
            UV_ID,
            id="uv-group",
        ),
        pytest.param(
            _envelope(["uv add --optional dev 'httpx2>=2.16.0'"], UV_CHECK),
            HTTPX2,
            UV_ID,
            id="uv-optional",
        ),
        pytest.param(
            _envelope(
                [
                    "uv add --frozen 'httpx2>=2.16.0'",
                    "uv add --frozen --dev 'httpx2>=2.16.0'",
                    "uv lock",
                ],
                UV_CHECK,
            ),
            HTTPX2,
            UV_ID,
            id="uv-split-pin",
        ),
        pytest.param(_envelope([PIP_SED], [PIP_GREP]), PIP, PIP_ID, id="pip"),
        pytest.param(
            _envelope(
                [PIP_SED, "sed -i 's/^flask==3.0.0$/flask==3.0.1/' requirements-dev.txt"],
                [PIP_GREP],
            ),
            PIP,
            PIP_ID,
            id="pip-two-files",
        ),
        pytest.param(
            _envelope(
                ["sed -i 's/^flask==3.0.0$/flask==3.0.1/' requirements-dev.txt"],
                ["grep -qx 'flask==3.0.1' requirements-dev.txt"],
            ),
            PIP,
            PIP_ID,
            id="pip-dev-file-only",
        ),
        pytest.param(_envelope([NPM_INSTALL], [NPM_GREP]), NPM, NPM_ID, id="npm-scoped"),
        pytest.param(
            _envelope(
                [f"{NPM_INSTALL} --save-dev", "npm run build"],
                ["npm ci", "npx eslint .", "npx prettier --check .", "npm test", NPM_GREP],
            ),
            NPM,
            NPM_ID,
            id="npm-full",
        ),
        pytest.param(
            _envelope([NPM_INSTALL], ["npm ci", "npm test", NPM_GREP]),
            NPM,
            NPM_ID,
            id="npm-some-components",
        ),
    ],
)
def test_what_the_generator_emits_passes(
    envelope: dict[str, Any], fields: dict[str, Any], package_id: str
) -> None:
    assert bump_binding_violations(envelope, fields, package_id) == ()


REFUSED: list[Any] = [
    # uv
    pytest.param(_envelope(["uv add 'other>=1'"], UV_CHECK), HTTPX2, UV_ID, id="uv-other-package"),
    pytest.param(
        _envelope(["uv add 'httpx2>=2.16.1'"], UV_CHECK), HTTPX2, UV_ID, id="uv-version-off"
    ),
    pytest.param(
        _envelope(["uv add 'httpx2==2.16.0'"], UV_CHECK), HTTPX2, UV_ID, id="uv-not-a-floor"
    ),
    pytest.param(
        _envelope(["uv add 'httpx2>=2.16.0' other"], UV_CHECK), HTTPX2, UV_ID, id="uv-second-arg"
    ),
    pytest.param(
        _envelope(["uv add 'httpx2>=2.16.0'\n"], UV_CHECK), HTTPX2, UV_ID, id="uv-trailing-newline"
    ),
    pytest.param(
        _envelope(["uv add --frozen 'httpx2>=2.16.0'"], UV_CHECK),
        HTTPX2,
        UV_ID,
        id="uv-frozen-never-locked",
    ),
    pytest.param(
        _envelope(["uv add --frozen 'httpx2>=2.16.0'", "uv lock"], UV_CHECK),
        HTTPX2,
        UV_ID,
        id="uv-one-site-frozen",
    ),
    pytest.param(
        _envelope(
            ["uv add 'httpx2>=2.16.0'", "uv add --dev 'httpx2>=2.16.0'", "uv lock"], UV_CHECK
        ),
        HTTPX2,
        UV_ID,
        id="uv-split-pin-unfrozen",
    ),
    pytest.param(
        _envelope(
            ["uv add --frozen 'httpx2>=2.16.0'", "uv add --frozen --dev 'httpx2>=2.16.0'"], UV_CHECK
        ),
        HTTPX2,
        UV_ID,
        id="uv-split-pin-no-lock",
    ),
    pytest.param(
        _envelope(
            [
                "uv add --frozen 'httpx2>=2.16.0'",
                "uv add --frozen --dev 'httpx2>=2.16.0'",
                "uv lock --frozen",
            ],
            UV_CHECK,
        ),
        HTTPX2,
        UV_ID,
        id="uv-lock-frozen-is-not-generated",
    ),
    pytest.param(
        _envelope(
            ["uv add --frozen 'httpx2>=2.16.0'", "uv add --frozen 'httpx2>=2.16.0'", "uv lock"],
            UV_CHECK,
        ),
        HTTPX2,
        UV_ID,
        id="uv-repeated-command",
    ),
    pytest.param(_envelope(["uv lock"], UV_CHECK), HTTPX2, UV_ID, id="uv-lock-alone"),
    pytest.param(
        _envelope(
            [
                "uv add --frozen 'httpx2>=2.16.0'",
                "uv add --frozen 'httpx2>=2.16.0' other",
                "uv lock",
            ],
            UV_CHECK,
        ),
        HTTPX2,
        UV_ID,
        id="uv-split-pin-second-arg",
    ),
    pytest.param(
        _envelope(["uv add 'httpx2>=2.16.0'"], ["uv add 'other>=1'"]),
        HTTPX2,
        UV_ID,
        id="uv-verifier-bumps-something",
    ),
    # pip
    pytest.param(
        _envelope(["sed -i 's/^other==3.0.0$/other==3.0.1/' requirements.txt"], [PIP_GREP]),
        PIP,
        PIP_ID,
        id="pip-other-package",
    ),
    pytest.param(
        _envelope(["sed -i 's/^flask==3.0.0$/flask==3.0.2/' requirements.txt"], [PIP_GREP]),
        PIP,
        PIP_ID,
        id="pip-version-off",
    ),
    pytest.param(
        _envelope(["sed -i 's/^flask==2.9.9$/flask==3.0.1/' requirements.txt"], [PIP_GREP]),
        PIP,
        PIP_ID,
        id="pip-from-version-off",
    ),
    pytest.param(
        _envelope(["sed -i 's/^flask==3.0.0$/flask==3.0.1/' setup.txt"], [PIP_GREP]),
        PIP,
        PIP_ID,
        id="pip-other-file",
    ),
    pytest.param(
        _envelope([PIP_SED], ["grep -qx 'flask==9' requirements.txt"]),
        PIP,
        PIP_ID,
        id="pip-verifier-other-version",
    ),
    pytest.param(
        _envelope([PIP_SED], ["grep -qx 'flask==3.0.1' requirements-dev.txt"]),
        PIP,
        PIP_ID,
        id="pip-verifier-other-file",
    ),
    pytest.param(_envelope([PIP_SED, PIP_SED], [PIP_GREP]), PIP, PIP_ID, id="pip-repeated-command"),
    pytest.param(
        _envelope([PIP_SED, "sed -i 's/^other==1$/other==2/' requirements-dev.txt"], [PIP_GREP]),
        PIP,
        PIP_ID,
        id="pip-second-sed-of-something-else",
    ),
    # npm
    pytest.param(
        _envelope([NPM_INSTALL, "npm run evil"], [NPM_GREP]), NPM, NPM_ID, id="npm-other-script"
    ),
    pytest.param(
        _envelope(["npm install a@1 b@2 --save-exact"], [NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-two-packages",
    ),
    pytest.param(
        _envelope(["npm install @other/node@22.2.0 --save-exact"], [NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-scoped-name-mismatch",
    ),
    pytest.param(
        _envelope(["npm install node@22.2.0 --save-exact"], [NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-scope-dropped",
    ),
    pytest.param(
        _envelope(["npm install @types/node@22.2.1 --save-exact"], [NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-version-off",
    ),
    pytest.param(
        _envelope(["npm install @types/node@22.2.0"], [NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-not-exact",
    ),
    pytest.param(_envelope(["npm run build"], [NPM_GREP]), NPM, NPM_ID, id="npm-build-alone"),
    pytest.param(
        _envelope(["npm run build", NPM_INSTALL], [NPM_GREP]), NPM, NPM_ID, id="npm-build-first"
    ),
    pytest.param(
        _envelope([NPM_INSTALL, "npm run build", "npm run build"], [NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-build-twice",
    ),
    pytest.param(
        _envelope([NPM_INSTALL], ['grep -q \'"other": "1"\' package.json']),
        NPM,
        NPM_ID,
        id="npm-verifier-other-package",
    ),
    pytest.param(_envelope([NPM_INSTALL], ["npm ci"]), NPM, NPM_ID, id="npm-verifier-no-grep"),
    pytest.param(
        _envelope([NPM_INSTALL], ["npm test", "npm ci", NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-verifier-out-of-order",
    ),
    pytest.param(
        _envelope([NPM_INSTALL], ["npm run evil", NPM_GREP]),
        NPM,
        NPM_ID,
        id="npm-verifier-unknown",
    ),
    # any
    pytest.param(
        _envelope(["uv add 'httpx2>=2.16.0'"], UV_CHECK, "AlobarQuest/other"),
        HTTPX2,
        UV_ID,
        id="other-repository",
    ),
    pytest.param(_envelope([], UV_CHECK), HTTPX2, UV_ID, id="uv-empty"),
    pytest.param(_envelope([], [PIP_GREP]), PIP, PIP_ID, id="pip-empty"),
    pytest.param(_envelope([], [NPM_GREP]), NPM, NPM_ID, id="npm-empty"),
    pytest.param(
        _envelope([PIP_SED], [PIP_GREP]), PIP, UV_ID.replace("httpx2", "flask"), id="wrong-tooling"
    ),
    pytest.param(
        _envelope(["uv add 'httpx2>=2.16.0'"], UV_CHECK), HTTPX2, "not-a-standing-id", id="no-id"
    ),
]


@pytest.mark.parametrize(("envelope", "fields", "package_id"), REFUSED)
def test_anything_else_is_refused(
    envelope: dict[str, Any], fields: dict[str, Any], package_id: str
) -> None:
    assert bump_binding_violations(envelope, fields, package_id) != ()


def test_the_repository_compares_case_folded() -> None:
    envelope = _envelope(["uv add 'httpx2>=2.16.0'"], UV_CHECK, "alobarquest/ORCHESTRATOR")
    assert bump_binding_violations(envelope, HTTPX2, UV_ID) == ()


def test_a_bump_ridden_into_the_agent_s_vocabulary_is_refused() -> None:
    """`allowed_commands` is every command the agent may run, so an entry that only it holds
    is a second bump the binding on `mutation_commands` alone would never see."""
    envelope = _envelope(["uv add 'httpx2>=2.16.0'"], UV_CHECK)
    envelope["constraints"]["allowed_commands"].insert(1, "uv add 'other>=1'")
    assert bump_binding_violations(envelope, HTTPX2, UV_ID) != ()


def test_the_vocabulary_is_the_generator_s_in_its_order() -> None:
    envelope = _envelope(["uv add 'httpx2>=2.16.0'"], UV_CHECK)
    envelope["constraints"]["allowed_commands"].reverse()
    assert bump_binding_violations(envelope, HTTPX2, UV_ID) != ()


def test_the_pip_rewrite_starts_from_the_package_s_own_from_version() -> None:
    fields = _fields("flask", "3.1.0", "3.1.1")
    envelope = _envelope(
        ["sed -i 's/^flask==3.1.0$/flask==3.1.1/' requirements.txt"],
        ["grep -qx 'flask==3.1.1' requirements.txt"],
    )
    assert bump_binding_violations(envelope, fields, PIP_ID) == ()


@pytest.mark.parametrize("key", ["package", "from_version", "to_version", "target_repo"])
@pytest.mark.parametrize("value", [None, "", 7])
def test_an_unreadable_profile_field_is_refused(key: str, value: object) -> None:
    envelope = _envelope(["uv add 'httpx2>=2.16.0'"], UV_CHECK)
    assert bump_binding_violations(envelope, {**HTTPX2, key: value}, UV_ID) != ()


@pytest.mark.parametrize(
    "constraints",
    [
        None,
        {},
        {"mutation_commands": "uv lock", "target_repository": REPOSITORY},
        {
            "allowed_commands": ["uv add 'httpx2>=2.16.0'", "uv lock --check"],
            "mutation_commands": 7,
            "verify_commands": ["uv lock --check"],
            "target_repository": REPOSITORY,
        },
        {
            "allowed_commands": ["uv add 'httpx2>=2.16.0'", "uv lock --check"],
            "mutation_commands": [7],
            "verify_commands": ["uv lock --check"],
            "target_repository": REPOSITORY,
        },
        {
            "allowed_commands": ["uv add 'httpx2>=2.16.0'"],
            "mutation_commands": ["uv add 'httpx2>=2.16.0'"],
            "target_repository": REPOSITORY,
        },
        {
            "allowed_commands": ["uv add 'httpx2>=2.16.0'", "uv lock --check"],
            "mutation_commands": ["uv add 'httpx2>=2.16.0'"],
            "verify_commands": ["uv lock --check"],
            "target_repository": 7,
        },
        {
            "allowed_commands": ["uv add 'httpx2>=2.16.0'", 7],
            "mutation_commands": ["uv add 'httpx2>=2.16.0'"],
            "verify_commands": [7],
            "target_repository": REPOSITORY,
        },
    ],
)
def test_unreadable_constraints_are_refused(constraints: object) -> None:
    assert bump_binding_violations({"constraints": constraints}, HTTPX2, UV_ID) != ()


def test_a_package_name_is_not_a_pattern() -> None:
    """`.` in a name must match only `.`: a regular expression built from it unescaped would
    accept a different package."""
    fields = _fields("a.b", "1.0", "1.1")
    envelope = _envelope(["uv add 'axb>=1.1'"], UV_CHECK)
    assert bump_binding_violations(envelope, fields, "orchestrator-uv-a-b") != ()


def test_the_function_does_not_change_its_input() -> None:
    envelope = _envelope(["uv add 'httpx2>=2.16.0'"], UV_CHECK)
    before = deepcopy(envelope)
    bump_binding_violations(envelope, HTTPX2, UV_ID)
    assert envelope == before
