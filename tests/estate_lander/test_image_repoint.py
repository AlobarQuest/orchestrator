"""The estate lander's hosting-platform and registry clients (Landing Part 3b, Task 4).

`CoolifyImages` may list applications, and -- for the applications the member record lists for ONE
repository, matched by host -- read one, change its image tag, and start a deploy of it. Nothing
else: every other path and verb is refused before the transport sees a byte (review focus 4). The
allowlist is derived from the member record each pass, never hand-kept.

`Registry` resolves a tag of one of this estate's GHCR images to its digest, and reads nothing else.

Everything runs against `httpx.MockTransport`; nothing reaches a network.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from estate_lander import image_repoint
from estate_lander.image_repoint import (
    TAG_FIELD,
    AppSpec,
    CoolifyImages,
    Registry,
    RegistryUnreadable,
    RepointRefused,
)

URL = "http://100.92.138.19:8000"
TOKEN = "throwaway-coolify-token-for-tests"
PAT = "throwaway-github-token-for-tests"
PARENT = "80b620ce1799c1eeeaac83f494e6cd9a553d5503"
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/alobarquest/brain"


def _row(uuid: str, fqdn: str | None, *, image: str = IMAGE, tag: str = "latest") -> dict:
    return {
        "uuid": uuid,
        "name": f"app-{uuid}",
        "fqdn": fqdn,
        "docker_registry_image_name": image,
        "docker_registry_image_tag": tag,
        "status": "running:healthy",
        # A field the listing carries that must never be echoed anywhere.
        "manual_webhook_secret_github": "never-echo-this",
    }


LISTING = [
    _row("u1", "https://app-brain.devonwatkins.com"),
    _row("u2", "https://code-brain.devonwatkins.com,https://code.example.invalid"),
    _row("u3", "https://change-mgr.alobar.net", image="ghcr.io/alobarquest/change-manager"),
    _row("u4", None),
]
BRAIN_APPS = (
    AppSpec("app-brain", "https://app-brain.devonwatkins.com/api/health"),
    AppSpec("code-brain", "https://CODE-brain.devonwatkins.com/api/health"),
)


class Platform:
    """A recording Coolify: answers the listing, one application, a PATCH and a deploy."""

    def __init__(self, listing: list[dict] | None = None, *, status: int = 200) -> None:
        self.listing = LISTING if listing is None else listing
        self.status = status
        self.requests: list[tuple[str, str, bytes]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.content))
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["User-Agent"].startswith("estate-lander/")
        if request.url.path == "/api/v1/applications":
            return httpx.Response(200, json=self.listing)
        uuid = request.url.path.split("/")[4]
        row = next(r for r in self.listing if r["uuid"] == uuid)
        if request.method == "GET":
            return httpx.Response(200, json=row)
        return httpx.Response(self.status, json={"message": f"echo {TOKEN}"})


def _client(platform: Platform) -> CoolifyImages:
    return CoolifyImages(URL, TOKEN, transport=httpx.MockTransport(platform))


# --------------------------------------------------------------------------------------------
# The surface (review focus 4).
# --------------------------------------------------------------------------------------------

ALLOWED = [
    ("GET", "/api/v1/applications"),
    ("GET", "/api/v1/applications/u1"),
    ("PATCH", "/api/v1/applications/u1"),
    ("POST", "/api/v1/applications/u1/start"),
    ("GET", "/api/v1/applications/u2"),
    ("PATCH", "/api/v1/applications/u2"),
    ("POST", "/api/v1/applications/u2/start"),
]
REFUSED = [
    # Another repository's application, and one nobody matched.
    ("GET", "/api/v1/applications/u3"),
    ("PATCH", "/api/v1/applications/u3"),
    ("POST", "/api/v1/applications/u3/start"),
    ("PATCH", "/api/v1/applications/u9"),
    # Everything else an allowed application has.
    ("DELETE", "/api/v1/applications/u1"),
    ("POST", "/api/v1/applications/u1"),
    ("PUT", "/api/v1/applications/u1"),
    ("GET", "/api/v1/applications/u1/start"),
    ("POST", "/api/v1/applications/u1/restart"),
    ("POST", "/api/v1/applications/u1/stop"),
    ("GET", "/api/v1/applications/u1/envs"),
    ("PATCH", "/api/v1/applications/u1/envs"),
    ("PATCH", "/api/v1/applications/u1/envs/bulk"),
    ("POST", "/api/v1/applications/u1/storages"),
    ("GET", "/api/v1/applications/u1/logs"),
    ("PATCH", "/api/v1/applications/u1/"),
    ("POST", "/api/v1/applications/u1/start/"),
    ("PATCH", "/api/v1/applications/U1"),
    # The platform's other surfaces.
    ("PATCH", "/api/v1/applications"),
    ("POST", "/api/v1/applications"),
    ("GET", "/api/v1/deploy"),
    ("POST", "/api/v1/deploy"),
    ("GET", "/api/v1/servers"),
    ("PATCH", "/api/v1/services/u1"),
    ("POST", "/api/v1/databases/u1/restart"),
    ("DELETE", "/api/v1/security/keys/u1"),
]
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _corpus() -> set[str]:
    paths = {path for _, path in ALLOWED + REFUSED}
    return paths | {f"{path}/" for path in paths} | {f"{path}x" for path in paths}


def disagreements(permits: Callable[[str, str], bool]) -> set[tuple[str, str, bool]]:
    """Each (method, path, accepted) where the predicate and the listed surface differ."""
    allowed = set(ALLOWED)
    return {
        (method, path, permits(method, path))
        for method in METHODS
        for path in _corpus()
        if permits(method, path) != ((method, path) in allowed)
    }


def test_the_confined_client_permits_exactly_the_listed_surface() -> None:
    client = _client(Platform())
    client.confine(BRAIN_APPS)

    assert disagreements(client.permits) == set()


def test_control_a_widened_predicate_fails_the_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison is not vacuous: widen the predicate to restart and the surface reports it."""
    client = _client(Platform())
    client.confine(BRAIN_APPS)
    original = image_repoint._permits
    monkeypatch.setattr(
        image_repoint,
        "_permits",
        lambda allowed, method, path: path.endswith("/restart") or original(allowed, method, path),
    )

    assert ("POST", "/api/v1/applications/u1/restart", True) in disagreements(client.permits)


def test_before_confining_only_the_listing_is_permitted() -> None:
    client = _client(Platform())

    assert [(m, p) for m, p in ALLOWED if client.permits(m, p)] == [("GET", "/api/v1/applications")]


def test_a_refused_request_leaves_nothing_on_the_wire() -> None:
    platform = Platform()
    client = _client(platform)
    client.confine(BRAIN_APPS)

    with pytest.raises(RepointRefused):
        client.set_tag("u3", PARENT)

    assert [m for m, _, _ in platform.requests] == ["GET"]


# --------------------------------------------------------------------------------------------
# The derived allowlist.
# --------------------------------------------------------------------------------------------


def test_each_member_app_is_matched_to_the_one_application_serving_its_host() -> None:
    client = _client(Platform())

    applications = client.confine(BRAIN_APPS)

    assert [(a.uuid, a.name, a.image, a.tag) for a in applications] == [
        ("u1", "app-brain", IMAGE, "latest"),
        ("u2", "code-brain", IMAGE, "latest"),
    ]


def test_an_app_no_application_serves_refuses_the_whole_repoint() -> None:
    client = _client(Platform())

    with pytest.raises(RepointRefused, match="infra-brain") as raised:
        client.confine((*BRAIN_APPS, AppSpec("infra-brain", "https://infra-brain.x/api/health")))

    assert "no application" in str(raised.value)
    assert client.permits("PATCH", "/api/v1/applications/u1") is False


def test_an_app_two_applications_serve_refuses_the_whole_repoint() -> None:
    twice = [*LISTING, _row("u5", "https://app-brain.devonwatkins.com")]

    with pytest.raises(RepointRefused, match="2 applications"):
        _client(Platform(twice)).confine(BRAIN_APPS)


def test_no_app_at_all_refuses() -> None:
    with pytest.raises(RepointRefused):
        _client(Platform()).confine(())


def test_a_second_confine_is_refused() -> None:
    client = _client(Platform())
    client.confine(BRAIN_APPS)

    with pytest.raises(RepointRefused):
        client.confine(BRAIN_APPS)


@pytest.mark.parametrize("body", [{"not": "a list"}, ["not an object"], [{"uuid": 7}]])
def test_a_listing_that_does_not_read_refuses(body: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = CoolifyImages(URL, TOKEN, transport=httpx.MockTransport(handler))

    with pytest.raises(RepointRefused):
        client.confine(BRAIN_APPS)


# --------------------------------------------------------------------------------------------
# The three acts.
# --------------------------------------------------------------------------------------------


def test_setting_a_tag_sends_only_the_tag_field() -> None:
    platform = Platform()
    client = _client(platform)
    client.confine(BRAIN_APPS)

    client.set_tag("u1", PARENT)
    client.start("u1")

    patch, start = platform.requests[1:]
    assert patch[:2] == ("PATCH", "/api/v1/applications/u1")
    assert json.loads(patch[2]) == {TAG_FIELD: PARENT}
    assert start == ("POST", "/api/v1/applications/u1/start", b"")


def test_reading_an_application_answers_its_image_and_tag() -> None:
    client = _client(Platform())
    client.confine(BRAIN_APPS)

    assert (client.application("u2").image, client.application("u2").tag) == (IMAGE, "latest")


@pytest.mark.parametrize("act", ["set_tag", "start"])
def test_a_refused_act_names_the_status_and_never_the_body_or_the_token(act: str) -> None:
    client = _client(Platform(status=500))
    client.confine(BRAIN_APPS)

    with pytest.raises(RepointRefused) as raised:
        client.set_tag("u1", PARENT) if act == "set_tag" else client.start("u1")

    assert "500" in str(raised.value) and TOKEN not in str(raised.value)


def test_a_platform_that_does_not_answer_is_refused_by_the_error_type_alone() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(f"timed out with {TOKEN}", request=request)

    client = CoolifyImages(URL, TOKEN, transport=httpx.MockTransport(handler))

    with pytest.raises(RepointRefused) as raised:
        client.confine(BRAIN_APPS)

    assert "ConnectTimeout" in str(raised.value) and TOKEN not in str(raised.value)


@pytest.mark.parametrize(
    "url", ["http://coolify-1.devonwatkins.com", "http://192.168.1.4:8000", "ftp://100.92.1.1"]
)
def test_the_token_is_sent_only_over_tls_or_to_a_tailnet_address(url: str) -> None:
    with pytest.raises(RepointRefused, match="must be https"):
        CoolifyImages(url, TOKEN)


# --------------------------------------------------------------------------------------------
# The registry.
# --------------------------------------------------------------------------------------------


class Ghcr:
    def __init__(self, tags: dict[str, str] | None = None, *, token_status: int = 200) -> None:
        self.tags = {PARENT: DIGEST} if tags is None else tags
        self.token_status = token_status
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        if request.url.path == "/token":
            assert request.headers["Authorization"].startswith("Basic ")
            assert request.url.params["scope"] == "repository:alobarquest/brain:pull"
            return httpx.Response(self.token_status, json={"token": "registry-token"})
        assert request.headers["Authorization"] == "Bearer registry-token"
        assert "application/vnd.oci.image.index.v1+json" in request.headers["Accept"]
        tag = request.url.path.rsplit("/", 1)[1]
        if tag not in self.tags:
            return httpx.Response(404)
        return httpx.Response(200, headers={"Docker-Content-Digest": self.tags[tag]})


def test_a_tag_resolves_to_its_digest() -> None:
    ghcr = Ghcr()

    digest = Registry(PAT, transport=httpx.MockTransport(ghcr)).digest(IMAGE, PARENT)

    assert digest == DIGEST
    assert [method for method, _ in ghcr.requests] == ["GET", "HEAD"]
    assert ghcr.requests[1][1] == f"https://ghcr.io/v2/alobarquest/brain/manifests/{PARENT}"


def test_a_tag_the_registry_does_not_hold_answers_none() -> None:
    assert Registry(PAT, transport=httpx.MockTransport(Ghcr({}))).digest(IMAGE, PARENT) is None


def test_a_registry_that_will_not_issue_a_token_is_unreadable_never_absent() -> None:
    registry = Registry(PAT, transport=httpx.MockTransport(Ghcr(token_status=401)))

    with pytest.raises(RegistryUnreadable) as raised:
        registry.digest(IMAGE, PARENT)

    assert PAT not in str(raised.value)


@pytest.mark.parametrize(
    ("image", "tag"),
    [
        ("docker.io/library/nginx", PARENT),
        ("ghcr.io/someone-else/brain", PARENT),
        ("ghcr.io/alobarquest/brain/../x", PARENT),
        (IMAGE, "../../token"),
        (IMAGE, "latest?x=1"),
    ],
)
def test_the_registry_reads_only_this_estates_images(image: str, tag: str) -> None:
    ghcr = Ghcr()

    with pytest.raises(RegistryUnreadable):
        Registry(PAT, transport=httpx.MockTransport(ghcr)).digest(image, tag)

    assert ghcr.requests == []


REGISTRY_ALLOWED = [
    ("GET", "/token"),
    ("HEAD", f"/v2/alobarquest/brain/manifests/{PARENT}"),
    ("HEAD", "/v2/alobarquest/change-manager/manifests/main"),
]
REGISTRY_REFUSED = [
    ("POST", "/token"),
    ("GET", f"/v2/alobarquest/brain/manifests/{PARENT}"),
    ("PUT", f"/v2/alobarquest/brain/manifests/{PARENT}"),
    ("DELETE", f"/v2/alobarquest/brain/manifests/{PARENT}"),
    ("HEAD", f"/v2/someone-else/brain/manifests/{PARENT}"),
    ("HEAD", "/v2/alobarquest/brain/blobs/sha256:abc"),
    ("HEAD", "/v2/alobarquest/brain/tags/list"),
    ("GET", "/v2/alobarquest/brain/tags/list"),
    ("HEAD", "/v2/alobarquest/../manifests/x"),
    ("HEAD", "/v2/alobarquest/brain/manifests/a/b"),
    ("GET", "/v2/"),
]


@pytest.mark.parametrize(("method", "path"), REGISTRY_ALLOWED)
def test_the_registry_permits_its_two_reads(method: str, path: str) -> None:
    assert image_repoint._registry_permits(method, path)


@pytest.mark.parametrize(("method", "path"), REGISTRY_REFUSED)
def test_the_registry_refuses_everything_else(method: str, path: str) -> None:
    assert not image_repoint._registry_permits(method, path)
