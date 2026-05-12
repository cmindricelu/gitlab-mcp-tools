import re
from urllib.parse import quote
import requests


class GitLabClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self._headers = {"PRIVATE-TOKEN": token}

    def _get_paginated(self, path: str, params: dict | None = None) -> list:
        url = f"{self.base_url}/api/v4{path}"
        results = []
        page = 1
        while True:
            p = {**(params or {}), "per_page": 100, "page": page}
            r = requests.get(url, headers=self._headers, params=p, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        return results

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{self.base_url}/api/v4{path}"
        r = requests.get(url, headers=self._headers, params=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/api/v4{path}"
        r = requests.post(
            url,
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/api/v4{path}"
        r = requests.put(
            url,
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # --- Projects ---

    def get_project_id(self, project_path: str) -> int:
        encoded = quote(project_path, safe="")
        data = self._get(f"/projects/{encoded}")
        return data["id"]

    # --- Merge Requests ---

    def list_mrs(self, project_id: int, state: str = "opened") -> list:
        return self._get_paginated(
            f"/projects/{project_id}/merge_requests", {"state": state}
        )

    def get_mr(self, project_id: int, mr_iid: int) -> dict:
        return self._get(f"/projects/{project_id}/merge_requests/{mr_iid}")

    def get_mr_versions(self, project_id: int, mr_iid: int) -> dict:
        versions = self._get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/versions"
        )
        if not versions or not isinstance(versions, list):
            raise ValueError(f"No versions found for MR !{mr_iid}")
        latest = versions[0]
        return {
            "base_sha": latest["base_commit_sha"],
            "start_sha": latest["start_commit_sha"],
            "head_sha": latest["head_commit_sha"],
        }

    def get_mr_diffs(self, project_id: int, mr_iid: int) -> list:
        data = self._get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/diffs",
            {"per_page": 100},
        )
        if isinstance(data, dict):
            return data.get("diffs", [])
        return data

    def get_mr_discussions(self, project_id: int, mr_iid: int) -> list:
        return self._get_paginated(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions"
        )

    def get_mr_commits(self, project_id: int, mr_iid: int) -> list:
        return self._get_paginated(
            f"/projects/{project_id}/merge_requests/{mr_iid}/commits"
        )

    def get_mr_pipelines(self, project_id: int, mr_iid: int) -> list:
        return self._get_paginated(
            f"/projects/{project_id}/merge_requests/{mr_iid}/pipelines"
        )

    # --- Notes & Discussions ---

    def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
        return self._post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
            {"body": body},
        )

    def post_discussion(
        self, project_id: int, mr_iid: int, body: str, position: dict | None = None
    ) -> dict:
        payload: dict = {"body": body}
        if position:
            payload["position"] = position
        return self._post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions", payload
        )

    def resolve_discussion(
        self, project_id: int, mr_iid: int, discussion_id: str, resolved: bool = True
    ) -> dict:
        return self._put(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions/{discussion_id}",
            {"resolved": resolved},
        )


# ---------------------------------------------------------------------------
# Diff helpers (ported from the AI review script)
# ---------------------------------------------------------------------------

def parse_diff_with_lines(diff_text: str) -> list[dict]:
    lines = []
    old_line: int | None = None
    new_line: int | None = None

    for raw in diff_text.splitlines():
        hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(2))
            continue
        if raw.startswith("---") or raw.startswith("+++"):
            continue
        if old_line is None:
            continue

        if raw.startswith("+"):
            lines.append({"old_line": None, "new_line": new_line, "content": raw[1:], "type": "+"})
            new_line += 1
        elif raw.startswith("-"):
            lines.append({"old_line": old_line, "new_line": None, "content": raw[1:], "type": "-"})
            old_line += 1
        else:
            content = raw[1:] if raw.startswith(" ") else raw
            lines.append({"old_line": old_line, "new_line": new_line, "content": content, "type": " "})
            old_line += 1
            new_line += 1

    return lines


def build_annotated_diff(file_path: str, diff_text: str) -> str:
    parsed = parse_diff_with_lines(diff_text)
    out = [f"FILE: {file_path}"]
    for e in parsed:
        t, nl, ol, content = e["type"], e["new_line"], e["old_line"], e["content"]
        if t == "+":
            out.append(f"[new:{nl}] +{content}")
        elif t == "-":
            out.append(f"[old:{ol}] -{content}")
        else:
            out.append(f"[ctx:{nl}/{ol}] {content}")
    return "\n".join(out)


def find_file_diff(diffs: list[dict], file_path: str) -> dict | None:
    for d in diffs:
        np = d.get("new_path", "")
        op = d.get("old_path", "")
        if np == file_path or op == file_path:
            return d
        if np.endswith(file_path) or file_path.endswith(np):
            return d
    return None
