"""Reading a /review refusal the way a person sees it: the refusal page, not a JSON body.

A refused /review request renders `refusal.html`, whose one `<section id="refusal">` carries a
`data-refusal-<field>` attribute for each field the API's error body would carry (`code`,
`message`, and `recovery`, `current-state`, `current-version` when set). These helpers read those
attributes back, unescaped, so a test asserts the code a person was shown.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

_PREFIX = "data-refusal-"


class _RefusalSection(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "section" and values.get("id") == "refusal":
            self.found.append(
                {
                    name.removeprefix(_PREFIX): value or ""
                    for name, value in attrs
                    if name.startswith(_PREFIX)
                }
            )


def refusal(response: Any) -> dict[str, str]:
    """The refusal page's fields, by name without the `data-refusal-` prefix."""
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("text/html"), (response.status_code, content_type, response.text)
    parser = _RefusalSection()
    parser.feed(response.text)
    assert len(parser.found) == 1, f"expected one refusal section, found {len(parser.found)}"
    return parser.found[0]


def refusal_code(response: Any) -> str:
    """The code the refusal page shows."""
    return refusal(response)["code"]
