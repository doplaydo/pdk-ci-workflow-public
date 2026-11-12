"""Resolve the CI DRC project for gfp 2.x and print its slug.

gfp 2.x picks the DRC deck from a project in the API key's org, not from
the PDK name. CI passes the project with ``gfp check drc --project <slug>``
so no CI identifier is written to a pyproject.toml that ships to users.

If the project does not exist yet it is created with the portal PDK whose
package names contain ``--distribution`` (default: the ``[project]`` name in
./pyproject.toml). A project keeps the deck of the PDK it was created with,
so callers must pick a slug that only one PDK uses.

Prints nothing and exits 0 when the installed gdsfactoryplus is older than
2.0, which needs no project. Errors go to stderr as GitHub ``::error::``
lines, so stdout carries only the slug.
"""

import argparse
import importlib.metadata
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.gdsfactory.com/api/portal/sdk"


class DrcProjectError(Exception):
    pass


def normalize(name: str) -> str:
    """PEP 503 distribution-name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def needs_project() -> bool:
    try:
        version = importlib.metadata.version("gdsfactoryplus")
    except importlib.metadata.PackageNotFoundError:
        return False
    return int(version.split(".")[0]) >= 2


def _request(path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{API}/{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "x-api-key": os.environ["GFP_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"{}")


def exists(slug: str) -> bool:
    try:
        _request(f"v2/projects/get_by_slug?{urllib.parse.urlencode({'slug': slug})}")
    except urllib.error.HTTPError:
        return False
    return True


def pdk_id(distribution: str) -> str:
    for pdk in _request("v1/pdks/list").get("data", []):
        if normalize(distribution) in map(normalize, pdk.get("package_names", [])):
            return pdk["id"]
    raise DrcProjectError(f"No DRC PDK for {distribution} is available to this API key")


def ensure(slug: str, distribution: str) -> str:
    if exists(slug):
        return slug
    pdk = pdk_id(distribution)
    try:
        response = _request(
            "v2/projects/create", {"name": slug, "slug": slug, "pdk_id": pdk}
        )
    except urllib.error.HTTPError as error:
        response = error.read().decode(errors="replace")
    # Re-check rather than parse: a parallel CI job may have created it first.
    if not exists(slug):
        raise DrcProjectError(f"Creating DRC project {slug} failed: {response}")
    return slug


def root_distribution() -> str:
    try:
        with open("pyproject.toml", "rb") as file:
            return tomllib.load(file)["project"]["name"]
    except (OSError, KeyError) as error:
        raise DrcProjectError("pyproject.toml has no [project] name") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--distribution")
    args = parser.parse_args()
    if not needs_project():
        return
    try:
        if not os.environ.get("GFP_API_KEY"):
            raise DrcProjectError(
                "GFP_API_KEY is not set; gfp 2.x DRC needs it to resolve a project"
            )
        print(ensure(args.slug.lower(), args.distribution or root_distribution()))
    except (DrcProjectError, urllib.error.URLError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
