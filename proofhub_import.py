from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app import (
    DEFAULT_BASE_URL,
    DEFAULT_COMPANY_URL,
    DEFAULT_PROJECT_ID,
    DEFAULT_TASKLIST_ID,
    ProofHubClient,
    execute_tasks,
    flatten_preview,
    parse_input,
    parse_status_map,
    validate_execution,
)


DEFAULT_CREATE_TASK_PATH = "/projects/{project_id}/todolists/{tasklist_id}/tasks"
DEFAULT_CREATE_SUBTASK_PATH = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}/subtasks"
DEFAULT_UPDATE_TASK_PATH = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}"
DEFAULT_STATUS_MAP = "todo=To Do\nin progress=In Progress\ndone=Completed\nblocked=Blocked"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import structured task text into ProofHub.")
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--run", action="store_true", help="Call ProofHub. Without this flag, only prints payload preview.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--tasklist-id", default=DEFAULT_TASKLIST_ID)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--company-url", default=DEFAULT_COMPANY_URL)
    parser.add_argument("--api-key-header", default="X-API-KEY")
    parser.add_argument("--create-task-path", default=DEFAULT_CREATE_TASK_PATH)
    parser.add_argument("--create-subtask-path", default=DEFAULT_CREATE_SUBTASK_PATH)
    parser.add_argument("--update-task-path", default=DEFAULT_UPDATE_TASK_PATH)
    args = parser.parse_args()

    raw_text = args.input_file.read_text(encoding="utf-8")
    defaults = {"project_id": args.project_id, "tasklist_id": args.tasklist_id}
    status_map = parse_status_map(DEFAULT_STATUS_MAP)
    parse_result = parse_input(raw_text, defaults)
    errors = validate_execution(parse_result)
    if errors:
        print("Validation errors:")
        for error in errors:
            print(f"- {error}")
        return 2

    preview = flatten_preview(parse_result.tasks, status_map)
    print(f"Parsed {len(parse_result.tasks)} parent tasks and {len(preview) - len(parse_result.tasks)} subtasks.")
    print(json.dumps([row["payload"] for row in preview], indent=2, default=str))

    if not args.run:
        print("Dry run only. Pass --run and set PROOFHUB_API_KEY to call ProofHub.")
        return 0

    api_key = os.environ.get("PROOFHUB_API_KEY", "").strip()
    if not api_key:
        print("Missing PROOFHUB_API_KEY environment variable.")
        return 3

    client = ProofHubClient(api_key, args.base_url, args.api_key_header, args.company_url)
    logs = execute_tasks(
        client,
        parse_result,
        status_map,
        args.create_task_path,
        args.create_subtask_path,
        args.update_task_path,
    )
    print(json.dumps(logs, indent=2, default=str))
    return 1 if any(log.get("level") == "error" for log in logs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
