"""Holds: a version ceiling on one dependency in one member repository.

A hold is keyed `(ecosystem, directory, dependency)` within its member and spelled as Dependabot
spells it (`npm_and_yarn`, `pip`, `uv`, `docker`, `github_actions`, ...). Only a confirmed member
proposal writes one (`proposals.py`).

**A VERSION THAT CANNOT BE READ IS NEVER "BELOW THE CEILING".** `release` returns `None` for
anything it cannot read, and every caller treats `None` as held (or, for a ceiling, refuses it).
"""

import re
from typing import Final

_RELEASE: Final = re.compile(r"v?(\d+(?:\.\d+)*)")

# Tooling and dependabot.yml spellings whose Dependabot ecosystem name differs, each mapped to the
# name a person meant. A hold typed with one would match no update, so validation refuses it.
# not-a-vocabulary: refused spellings with a hint; Dependabot's names themselves are not enumerated
MISSPELLED_ECOSYSTEMS: Final = {
    "npm": "npm_and_yarn",
    "yarn": "npm_and_yarn",
    "pnpm": "npm_and_yarn",
    "github-actions": "github_actions",
}


def release(version: str) -> tuple[int, ...] | None:
    """The leading dotted numeric release of `version`, or `None` when it has none.

    A leading `v` is stripped, and anything after the release is ignored when it starts a
    pre-release, build or post-release part (`2.0.55rc1`, `1.2.3-beta.1`, `1.2.3+b5`,
    `1.2.3.post1` all read as their release). Refused, so `None`: no leading digit (`latest`,
    `^1.2`, `>=1.2`), a release that ends in a dot or doubles one (`1.`, `2..0`), and a PEP 440
    epoch (`1!2.0`), which would otherwise read as release `(1,)` and compare low.
    """
    text = version.strip()
    match = _RELEASE.match(text)
    if match is None:
        return None
    rest = text[match.end() :]
    if rest.startswith("!") or rest == "." or rest.startswith(".."):
        return None
    return tuple(int(part) for part in match.group(1).split("."))
