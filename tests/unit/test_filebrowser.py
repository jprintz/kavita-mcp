import httpx
import pytest

from kavita_mcp.filebrowser import FileBrowserClient, UploadServiceError, UploadUnreachable


class FakeFileBrowser:
    """Answers like File Browser v2.63 with noauth; counts logins."""

    def __init__(self):
        self.logins = 0
        self.valid: set[str] = set()
        self.files: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.reject_next = 0  # answer 401 this many more times, even with a valid token

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/login":
            assert request.content == b"{}"
            self.logins += 1
            token = f"token-{self.logins}"
            self.valid.add(token)
            return httpx.Response(200, text=token)
        if self.reject_next or request.headers.get("X-Auth") not in self.valid:
            self.reject_next = max(0, self.reject_next - 1)
            return httpx.Response(401)
        if path.startswith("/api/raw/"):
            name = path[len("/api/raw") :]
            return (
                httpx.Response(200, content=self.files[name])
                if name in self.files
                else httpx.Response(404)
            )
        name = path[len("/api/resources") :]
        if request.method == "POST":
            if name in self.files and request.url.params.get("override") != "true":
                return httpx.Response(409)
            self.files[name] = request.content
            return httpx.Response(200)
        if request.method == "DELETE":
            hit = [k for k in self.files if k.startswith(name)]
            for k in hit:
                del self.files[k]
            return httpx.Response(204) if hit else httpx.Response(404)
        if request.method == "GET":
            children = sorted(
                {k[len(name) :].split("/")[0] for k in self.files if k.startswith(name)}
            )
            if not children and name not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, json={"items": [{"name": c} for c in children]})
        return httpx.Response(405)


def make(fake=None):
    fake = fake or FakeFileBrowser()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return FileBrowserClient("http://kavita-upload:8080", http=http), fake


async def test_logs_in_once_and_sends_x_auth():
    client, fake = make()
    await client.upload("/Reports/q3/q3.epub", b"one")
    await client.exists("/Reports/q3/q3.epub")
    assert fake.logins == 1
    assert {r.headers.get("X-Auth") for r in fake.requests if r.url.path != "/api/login"} == {
        "token-1"
    }


async def test_relogin_once_on_401():
    client, fake = make()
    await client.upload("/Reports/a/a.epub", b"x")
    fake.valid.clear()  # the 2-hour token expired
    await client.upload("/Reports/b/b.epub", b"y")
    assert fake.logins == 2
    assert fake.files["/Reports/b/b.epub"] == b"y"


async def test_second_401_raises():
    client, fake = make()
    fake.reject_next = 2
    with pytest.raises(UploadServiceError):
        await client.upload("/Reports/a/a.epub", b"x")


async def test_upload_overwrites():
    client, fake = make()
    await client.upload("/Reports/q3/q3.epub", b"old")
    await client.upload("/Reports/q3/q3.epub", b"new")
    assert fake.files["/Reports/q3/q3.epub"] == b"new"
    post = [r for r in fake.requests if r.method == "POST" and r.url.path.startswith("/api/res")]
    assert all(r.url.params["override"] == "true" for r in post)


async def test_download():
    client, _ = make()
    await client.upload("/Reports/q3/q3.epub", b"bytes")
    assert await client.download("/Reports/q3/q3.epub") == b"bytes"


async def test_delete_and_missing():
    client, fake = make()
    await client.upload("/Reports/q3/q3.epub", b"x")
    assert await client.delete("/Reports/q3/") is True
    assert fake.files == {}
    assert await client.delete("/Reports/q3/") is False


async def test_exists():
    client, _ = make()
    assert await client.exists("/Reports/q3/q3.epub") is False
    await client.upload("/Reports/q3/q3.epub", b"x")
    assert await client.exists("/Reports/q3/q3.epub") is True


async def test_list_dir():
    client, _ = make()
    await client.upload("/Reports/a/a.epub", b"x")
    await client.upload("/Reports/b/b.epub", b"y")
    assert await client.list_dir("/Reports/") == ["a", "b"]


async def test_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client, _ = make(refuse)
    with pytest.raises(UploadUnreachable):
        await client.exists("/Reports/x/x.epub")


async def test_server_error_raises():
    client, _ = make(
        lambda r: (
            httpx.Response(200, text="t") if r.url.path == "/api/login" else httpx.Response(500)
        )
    )
    with pytest.raises(UploadServiceError):
        await client.upload("/Reports/x/x.epub", b"x")
