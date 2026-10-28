"""First-run setup of the throwaway Kavita (scripts/throwaway.sh up).

Creates the admin, an auth key, folder watching, the Books and Reports libraries,
and a non-admin user "reader" with its own key. Writes .throwaway/env (mode 0600)
and .throwaway/admin.json. Never prints a key or a password.
"""

import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

KAVITA = "http://127.0.0.1:5055"
STATE = Path(".throwaway")


def library_body(name: str, folder: str) -> dict:
    # From the research notes (Kavita 0.9.1.4): type 2 = Book, metadataProvider 2.
    return {
        "name": name,
        "type": 2,
        "folders": [folder],
        "folderWatching": True,
        "includeInDashboard": True,
        "includeInSearch": True,
        "manageCollections": True,
        "manageReadingLists": True,
        "allowScrobbling": False,
        "allowMetadataMatching": False,
        "enableMetadata": True,
        "removePrefixForSortName": False,
        "inheritWebLinksFromFirstChapter": False,
        "defaultLanguage": "",
        "metadataProvider": 2,
        "fileGroupTypes": [2],
        "excludePatterns": [],
    }


def check(r: httpx.Response) -> httpx.Response:
    if r.is_error:
        raise SystemExit(f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:300]}")
    return r


def wait_healthy(client: httpx.Client) -> None:
    for _ in range(120):
        try:
            if client.get("/api/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    raise SystemExit("Kavita did not become healthy in 120 s")


def new_key(client: httpx.Client, token: str, name: str) -> str:
    r = client.post(
        "/api/Account/create-auth-key",
        headers={"Authorization": f"Bearer {token}"},
        json={"keyLength": 32, "name": name},
    )
    return check(r).json()["key"]


def main() -> None:
    client = httpx.Client(base_url=KAVITA, timeout=30)
    wait_healthy(client)

    password = secrets.token_urlsafe(18) + "Aa1!"
    user = check(
        client.post(
            "/api/Account/register",
            json={"username": "kadmin", "password": password, "email": ""},
        )
    ).json()
    admin_key = new_key(client, user["token"], "kavita-mcp")
    admin = {"x-api-key": admin_key}

    settings = check(client.get("/api/Settings", headers=admin)).json()
    settings["enableFolderWatching"] = True
    check(client.post("/api/Settings", headers=admin, json=settings))

    for name, folder in (("Books", "/books"), ("Reports", "/reports")):
        check(client.post("/api/Library/create", headers=admin, json=library_body(name, folder)))
    libraries = {
        lib["name"]: lib["id"]
        for lib in check(client.get("/api/Library/libraries", headers=admin)).json()
    }

    invite = check(
        client.post(
            "/api/Account/invite",
            headers=admin,
            json={
                "email": "reader@example.invalid",
                "roles": [],
                "libraries": [libraries["Books"], libraries["Reports"]],
                "ageRestriction": {"ageRating": -1, "includeUnknowns": True},
            },
        )
    ).json()
    query = parse_qs(urlparse(invite["emailLink"]).query)
    reader_password = secrets.token_urlsafe(18) + "Aa1!"
    reader = check(
        client.post(
            "/api/Account/confirm-email",
            json={
                "email": query["email"][0],
                "token": query["token"][0],
                "password": reader_password,
                "username": "reader",
            },
        )
    ).json()
    reader_key = new_key(client, reader["token"], "kavita-mcp")

    STATE.mkdir(exist_ok=True)
    env = {
        "KAVITA_URL": KAVITA,
        "KAVITA_API_KEY": admin_key,
        "KAVITA_NONADMIN_KEY": reader_key,
        "KAVITA_PUBLIC_URL": KAVITA,
        "FILEBROWSER_URL": "http://127.0.0.1:8088",
        "KAVITA_MCP_URL": "http://127.0.0.1:18000/mcp",
        "KAVITA_MCP_READER_URL": "http://127.0.0.1:18001/mcp",
        "KAVITA_MCP_BADKEY_URL": "http://127.0.0.1:18002/mcp",
        "BOOKS_LIBRARY_ID": str(libraries["Books"]),
        "REPORTS_LIBRARY_ID": str(libraries["Reports"]),
    }
    path = STATE / "env"
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    os.chmod(path, 0o600)
    admin_file = STATE / "admin.json"
    admin_file.write_text(json.dumps({"kadmin": password, "reader": reader_password}))
    os.chmod(admin_file, 0o600)
    print(
        f"throwaway ready: Books={libraries['Books']} Reports={libraries['Reports']}, keys in {path}"
    )


if __name__ == "__main__":
    main()
