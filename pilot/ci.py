"""Import a digest from the artifact of the trusted main-branch publish workflow."""
import io
import json
import os
import re
import zipfile
import httpx

def verify_run(repository, run_id):
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.getenv("GITHUB_READ_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    root = f"https://api.github.com/repos/{repository}/actions"
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        response = client.get(f"{root}/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        if not (run["conclusion"] == "success" and run["status"] == "completed"
                and run["event"] == "push" and run["head_branch"] == "main"
                and run["head_repository"]["full_name"].lower() == repository.lower()
                and run["path"] == ".github/workflows/publish.yml"):
            raise ValueError("A successful main-branch publish run is required")
        sha = run["head_sha"]
        if not re.fullmatch("[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit")
        response = client.get(f"{root}/runs/{run_id}/artifacts", params={"name":"releasepilot-image"})
        response.raise_for_status()
        items = [a for a in response.json()["artifacts"] if not a["expired"] and a["name"] == "releasepilot-image"]
        if len(items) != 1 or items[0]["size_in_bytes"] > 65536:
            raise ValueError("Missing or invalid build evidence")
        response = client.get(f"{root}/artifacts/{int(items[0]['id'])}/zip")
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            if archive.getinfo("image.json").file_size > 4096:
                raise ValueError("Oversized manifest")
            manifest = json.loads(archive.read("image.json"))
        image = manifest["image"]
        prefix = "ghcr.io/" + repository.lower() + "@sha256:"
        if manifest["sha"] != sha or not re.fullmatch(re.escape(prefix) + "[0-9a-f]{64}", image):
            raise ValueError("Build manifest does not match the repository and commit")
        return {"image":image, "sha":sha, "run_id":run_id}
