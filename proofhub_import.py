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
    label_coverage,
    label_map_from_records,
    label_map_text,
    labels_needed_for_parse_result,
    parse_input,
    parse_label_map,
    parse_status_map,
    sync_label_map,
    validate_execution,
)


DEFAULT_CREATE_TASK_PATH = "/projects/{project_id}/todolists/{tasklist_id}/tasks"
DEFAULT_CREATE_SUBTASK_PATH = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}/subtasks"
DEFAULT_UPDATE_TASK_PATH = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}"
DEFAULT_STATUS_MAP = "todo=To Do\nin progress=In Progress\ndone=Completed\nblocked=Blocked"
DEFAULT_LABELS_PATH = "/labels"
DEFAULT_CREATE_LABEL_PATH = "/labels"


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
    parser.add_argument("--label-map", default="", help="Newline or comma separated label=id mappings.")
    parser.add_argument("--labels-path", default=DEFAULT_LABELS_PATH)
    parser.add_argument("--create-label-path", default=DEFAULT_CREATE_LABEL_PATH)
    parser.add_argument("--fetch-labels", action="store_true", help="Fetch labels from ProofHub before preview/run.")
    parser.add_argument("--auto-create-missing-labels", action="store_true")
    args = parser.parse_args()

    raw_text = args.input_file.read_text(encoding="utf-8")
    defaults = {"project_id": args.project_id, "tasklist_id": args.tasklist_id}
    status_map = parse_status_map(DEFAULT_STATUS_MAP)
    parse_result = parse_input(raw_text, defaults)
    label_map = parse_label_map(args.label_map.replace(",", "\n"))

    api_key = os.environ.get("PROOFHUB_API_KEY", "").strip()
    client = None
    label_logs = []
    if args.fetch_labels or args.auto_create_missing_labels:
        if not api_key:
            print("Missing PROOFHUB_API_KEY environment variable for label fetching.")
            return 3
        client = ProofHubClient(api_key, args.base_url, args.api_key_header, args.company_url)
        if args.fetch_labels:
            label_map.update(label_map_from_records(client.list_labels(args.labels_path)))
        if args.auto_create_missing_labels:
            label_map, label_logs = sync_label_map(
                client,
                label_map,
                labels_needed_for_parse_result(parse_result),
                args.labels_path,
                args.create_label_path,
                True,
            )

    errors = validate_execution(parse_result)
    if errors:
        print("Validation errors:")
        for error in errors:
            print(f"- {error}")
        return 2

    preview = flatten_preview(parse_result.tasks, status_map, label_map)
    mapped_labels, missing_labels = label_coverage(parse_result, label_map)
    print(f"Parsed {len(parse_result.tasks)} parent tasks and {len(preview) - len(parse_result.tasks)} subtasks.")
    print(f"Labels mapped: {len(mapped_labels)}")
    if missing_labels:
        print("Missing label IDs:")
        for label in missing_labels:
            print(f"- {label}")
    if label_map:
        print("Effective label map:")
        print(label_map_text(label_map))
    print(json.dumps([row["payload"] for row in preview], indent=2, default=str))

    if not args.run:
        print("Dry run only. Pass --run and set PROOFHUB_API_KEY to call ProofHub.")
        return 0

    if not api_key:
        print("Missing PROOFHUB_API_KEY environment variable.")
        return 3

    if client is None:
        client = ProofHubClient(api_key, args.base_url, args.api_key_header, args.company_url)
    logs = execute_tasks(
        client,
        parse_result,
        status_map,
        label_map,
        args.create_task_path,
        args.create_subtask_path,
        args.update_task_path,
    )
    logs = label_logs + logs
    print(json.dumps(logs, indent=2, default=str))
    return 1 if any(log.get("level") == "error" for log in logs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
