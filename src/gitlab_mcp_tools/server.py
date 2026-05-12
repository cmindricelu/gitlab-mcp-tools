import json
import os
from typing import Literal

import requests
from mcp.server.fastmcp import FastMCP

from .gitlab import GitLabClient, build_annotated_diff, find_file_diff

mcp = FastMCP("gitlab")


def _client() -> GitLabClient:
    token = os.environ.get("GITLAB_TOKEN", "")
    if not token:
        raise RuntimeError("GITLAB_TOKEN environment variable is not set")
    url = os.environ.get("GITLAB_URL", "https://gitlab.com")
    return GitLabClient(url, token)


# ---------------------------------------------------------------------------
# Merge Requests — read
# ---------------------------------------------------------------------------

@mcp.tool()
def list_merge_requests(project_path: str, state: str = "opened") -> str:
    """
    List merge requests for a GitLab project.

    Args:
        project_path: Full project path, e.g. 'group/subgroup/project'
        state: 'opened' | 'closed' | 'merged' | 'all'
    """
    client = _client()
    pid = client.get_project_id(project_path)
    mrs = client.list_mrs(pid, state=state)
    summary = [
        {
            "iid": mr["iid"],
            "title": mr["title"],
            "state": mr["state"],
            "author": mr.get("author", {}).get("name"),
            "assignees": [a.get("name") for a in mr.get("assignees", [])],
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "created_at": mr.get("created_at"),
            "updated_at": mr.get("updated_at"),
            "web_url": mr.get("web_url"),
            "pipeline_status": (
                mr.get("head_pipeline", {}).get("status")
                if mr.get("head_pipeline")
                else None
            ),
        }
        for mr in mrs
    ]
    return json.dumps(summary, indent=2)


@mcp.tool()
def get_merge_request(project_path: str, mr_iid: int) -> str:
    """
    Get full details of a specific merge request.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID (internal project number shown as !N)
    """
    client = _client()
    pid = client.get_project_id(project_path)
    mr = client.get_mr(pid, mr_iid)
    return json.dumps(mr, indent=2)


@mcp.tool()
def get_mr_diff(project_path: str, mr_iid: int, annotated: bool = True) -> str:
    """
    Get the diff of a merge request.

    Annotated format marks each line with [new:N], [old:N], or [ctx:N/N] so an
    AI agent can reference exact line numbers when posting review comments.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
        annotated: True for AI-friendly annotated format; False for raw JSON diffs
    """
    client = _client()
    pid = client.get_project_id(project_path)
    diffs = client.get_mr_diffs(pid, mr_iid)

    if not annotated:
        return json.dumps(diffs, indent=2)

    parts = []
    for fd in diffs:
        path = fd.get("new_path") or fd.get("old_path", "")
        if fd.get("too_large"):
            parts.append(f"FILE: {path}\n(file too large — diff not available)")
            continue
        diff_text = fd.get("diff", "")
        if diff_text:
            parts.append(build_annotated_diff(path, diff_text))

    return "\n\n".join(parts)


@mcp.tool()
def get_mr_versions(project_path: str, mr_iid: int) -> str:
    """
    Get the base/start/head commit SHAs for a merge request.

    These SHAs are required when posting inline review comments via
    post_mr_review_thread. Fetch them once per MR review session.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
    """
    client = _client()
    pid = client.get_project_id(project_path)
    versions = client.get_mr_versions(pid, mr_iid)
    return json.dumps(versions, indent=2)


@mcp.tool()
def get_mr_commits(project_path: str, mr_iid: int) -> str:
    """
    List commits included in a merge request.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
    """
    client = _client()
    pid = client.get_project_id(project_path)
    commits = client.get_mr_commits(pid, mr_iid)
    summary = [
        {
            "short_id": c.get("short_id"),
            "title": c.get("title"),
            "author_name": c.get("author_name"),
            "created_at": c.get("created_at"),
        }
        for c in commits
    ]
    return json.dumps(summary, indent=2)


@mcp.tool()
def get_mr_pipelines(project_path: str, mr_iid: int) -> str:
    """
    Get CI/CD pipeline status for a merge request.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
    """
    client = _client()
    pid = client.get_project_id(project_path)
    pipelines = client.get_mr_pipelines(pid, mr_iid)
    summary = [
        {
            "id": p.get("id"),
            "status": p.get("status"),
            "ref": p.get("ref"),
            "sha": p.get("sha", "")[:8],
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
            "web_url": p.get("web_url"),
        }
        for p in pipelines
    ]
    return json.dumps(summary, indent=2)


@mcp.tool()
def list_mr_discussions(project_path: str, mr_iid: int) -> str:
    """
    List all discussion threads on a merge request.

    Useful for reading existing review comments before posting new ones,
    to avoid duplicates.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
    """
    client = _client()
    pid = client.get_project_id(project_path)
    discussions = client.get_mr_discussions(pid, mr_iid)
    return json.dumps(discussions, indent=2)


# ---------------------------------------------------------------------------
# Merge Requests — write
# ---------------------------------------------------------------------------

@mcp.tool()
def post_mr_comment(project_path: str, mr_iid: int, body: str) -> str:
    """
    Post a general comment (note) on a merge request.

    Use this for overall review summaries, approval messages, or any feedback
    that does not belong to a specific line of code.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
        body: Comment text (Markdown supported)
    """
    client = _client()
    pid = client.get_project_id(project_path)
    note = client.post_note(pid, mr_iid, body)
    return json.dumps({"note_id": note.get("id"), "body": note.get("body")}, indent=2)


@mcp.tool()
def post_mr_review_thread(
    project_path: str,
    mr_iid: int,
    body: str,
    file_path: str,
    line_number: int,
    line_type: Literal["N", "O"],
) -> str:
    """
    Post an inline review comment anchored to a specific line in the diff.

    The tool resolves the required GitLab SHAs automatically. Use line numbers
    exactly as shown in the [new:N] / [old:N] markers from get_mr_diff.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
        body: Review comment text (Markdown supported)
        file_path: File path as shown in the diff (new_path column)
        line_number: Line number from the annotated diff marker
        line_type: 'N' for new/added lines, 'O' for old/removed lines
    """
    client = _client()
    pid = client.get_project_id(project_path)

    versions = client.get_mr_versions(pid, mr_iid)
    diffs = client.get_mr_diffs(pid, mr_iid)

    file_diff = find_file_diff(diffs, file_path)
    if not file_diff:
        return json.dumps({"error": f"File not found in diff: {file_path}"})

    position: dict = {
        "position_type": "text",
        "base_sha": versions["base_sha"],
        "start_sha": versions["start_sha"],
        "head_sha": versions["head_sha"],
        "old_path": file_diff.get("old_path") or file_diff.get("new_path"),
        "new_path": file_diff.get("new_path") or file_diff.get("old_path"),
    }
    if line_type == "N":
        position["new_line"] = line_number
    else:
        position["old_line"] = line_number

    try:
        discussion = client.post_discussion(pid, mr_iid, body, position)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (400, 422):
            detail = exc.response.text[:300] if exc.response is not None else str(exc)
            return json.dumps(
                {"error": f"Invalid diff position (HTTP {status})", "detail": detail}
            )
        raise

    notes = discussion.get("notes", [])
    return json.dumps(
        {
            "discussion_id": discussion.get("id"),
            "note_id": notes[0]["id"] if notes else None,
        },
        indent=2,
    )


@mcp.tool()
def resolve_mr_discussion(
    project_path: str,
    mr_iid: int,
    discussion_id: str,
    resolved: bool = True,
) -> str:
    """
    Resolve or re-open a discussion thread on a merge request.

    Args:
        project_path: Full project path
        mr_iid: Merge request IID
        discussion_id: Discussion ID string (from list_mr_discussions)
        resolved: True to resolve, False to re-open
    """
    client = _client()
    pid = client.get_project_id(project_path)
    result = client.resolve_discussion(pid, mr_iid, discussion_id, resolved)
    return json.dumps(
        {"discussion_id": result.get("id"), "resolved": resolved}, indent=2
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
