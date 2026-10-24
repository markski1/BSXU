import json
from pathlib import Path
import sys
import tomllib
import urllib.request


def audit():
    lock = tomllib.loads((Path(__file__).resolve().parents[1] / "uv.lock").read_text(encoding="utf-8"))
    packages = [package for package in lock["package"] if package.get("source", {}).get("registry")]
    pending = [{"package": {"name": item["name"], "ecosystem": "PyPI"}, "version": item["version"]} for item in packages]
    findings = set()
    for _ in range(10):
        request = urllib.request.Request(
            "https://api.osv.dev/v1/querybatch", data=json.dumps({"queries": pending}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "BSXU-dependency-audit"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            results = json.load(response)["results"]
        if len(results) != len(pending):
            raise RuntimeError("OSV returned an incomplete response.")
        next_page = []
        for query, result in zip(pending, results):
            for vulnerability in result.get("vulns", []):
                findings.add((query["package"]["name"], query["version"], vulnerability["id"]))
            if result.get("next_page_token"):
                next_page.append(query | {"page_token": result["next_page_token"]})
        if not next_page:
            break
        pending = next_page
    else:
        raise RuntimeError("Dependency audit pagination did not complete.")
    for name, version, identifier in sorted(findings):
        print(f"{name}=={version}: https://osv.dev/vulnerability/{identifier}")
    print(f"Checked {len(packages)} locked packages; {len(findings)} advisory matches.")
    return bool(findings)


if __name__ == "__main__":
    try:
        raise SystemExit(audit())
    except (OSError, ValueError, KeyError, RuntimeError) as exception:
        print(f"Dependency audit could not complete: {exception}", file=sys.stderr)
        raise SystemExit(1)
