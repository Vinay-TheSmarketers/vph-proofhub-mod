from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import requests
import streamlit as st
import streamlit.components.v1 as components


DEFAULT_COMPANY_URL = "https://smarketers.proofhub.com"
DEFAULT_BASE_URL = f"{DEFAULT_COMPANY_URL}/api/v3"
DEFAULT_TIMEOUT = 30
DEFAULT_CONNECTION_TEST_PATH = "/projects"
DEFAULT_PROJECT_ID = "9572720073"
DEFAULT_TASKLIST_ID = "271269310285"
DEFAULT_PROVIDED_TASK_FILE = (
    r"C:\Users\OrCon\.codex\attachments\cf87045d-fad8-4611-a28f-549e1447733d\pasted-text-2.txt"
)
DEFAULT_SAMPLE_TEMPLATE = """Project: 9572720073
Tasklist: 271269310285

Task: Build frontend UI polish
Description: Tighten the layout, simplify the controls, and verify responsive behavior.
Status: in progress
Priority: medium
Labels: ui/ux, frontend
Start Date: today
Due Date: tomorrow
Assignees: 123456789

Subtask: Review desktop layout
Status: in progress
Due Date: today

Subtask: Test mobile responsiveness
Status: todo
Due Date: tomorrow

Task: New web scraper mini-app
Description: Standalone automation tool for collecting and validating metadata.
Status: todo
Priority: high
Labels: backend, seo
Due Date: next Friday
"""


ActionKind = Literal["create", "update"]
RoutingAction = Literal["update_existing", "create_task", "create_project"]


@dataclass
class RoutingDecision:
    action_type: RoutingAction
    target_bucket_id: str | None
    task_payload: dict[str, Any]
    routing_justification: str


@dataclass
class ParsedTask:
    action: ActionKind
    title: str
    task_id: str | None = None
    description: str = ""
    due_at: datetime | None = None
    start_at: datetime | None = None
    status: str | None = None
    priority: str | None = None
    labels: list[str] = field(default_factory=list)
    assignee_ids: list[str] = field(default_factory=list)
    project_id: str | None = None
    tasklist_id: str | None = None
    parent_id: str | None = None
    subtasks: list["ParsedTask"] = field(default_factory=list)
    raw: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    tasks: list[ParsedTask]
    warnings: list[str]
    defaults: dict[str, Any]


class ProofHubError(RuntimeError):
    def __init__(self, message: str, response: requests.Response | None = None) -> None:
        super().__init__(message)
        self.response = response
        self.status_code = response.status_code if response is not None else None
        self.body = _safe_response_text(response)


class ProofHubClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        auth_header: str,
        company_url: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                auth_header: api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "proofhub-task-orchestrator/1.0",
            }
        )
        company_url = company_url.strip()
        if company_url:
            self.session.headers.update(
                {
                    "X-COMPANY-URL": company_url,
                    "COMPANY-URL": company_url,
                    "company-url": company_url,
                }
            )

    def create_task(
        self,
        project_id: str,
        tasklist_id: str,
        payload: dict[str, Any],
        create_endpoint: str,
    ) -> dict[str, Any]:
        path = create_endpoint.format(project_id=project_id, tasklist_id=tasklist_id)
        return self._request("POST", path, json_body=payload)

    def create_project(self, payload: dict[str, Any], create_project_endpoint: str) -> dict[str, Any]:
        return self._request("POST", create_project_endpoint, json_body=payload)

    def create_tasklist(
        self,
        project_id: str,
        payload: dict[str, Any],
        create_tasklist_endpoint: str,
    ) -> dict[str, Any]:
        path = create_tasklist_endpoint.format(project_id=project_id)
        return self._request("POST", path, json_body=payload)

    def create_subtask(
        self,
        project_id: str,
        tasklist_id: str,
        task_id: str,
        payload: dict[str, Any],
        create_subtask_endpoint: str,
    ) -> dict[str, Any]:
        path = create_subtask_endpoint.format(
            project_id=project_id,
            tasklist_id=tasklist_id,
            task_id=task_id,
        )
        return self._request("POST", path, json_body=payload)

    def list_subtasks(
        self,
        project_id: str,
        tasklist_id: str,
        task_id: str,
        list_subtasks_endpoint: str,
    ) -> list[dict[str, Any]]:
        path = list_subtasks_endpoint.format(project_id=project_id, tasklist_id=tasklist_id, task_id=task_id)
        response = self._request("GET", path)
        return normalize_task_records(response)

    def update_task(
        self,
        project_id: str,
        tasklist_id: str,
        task_id: str,
        payload: dict[str, Any],
        update_endpoint: str,
    ) -> dict[str, Any]:
        path = update_endpoint.format(project_id=project_id, tasklist_id=tasklist_id, task_id=task_id)
        return self._request("PUT", path, json_body=payload)

    def list_tasks(self, project_id: str, tasklist_id: str, list_tasks_endpoint: str) -> list[dict[str, Any]]:
        path = list_tasks_endpoint.format(project_id=project_id, tasklist_id=tasklist_id)
        response = self._request("GET", path)
        return normalize_task_records(response)

    def check_connection(self, account_endpoint: str) -> dict[str, Any]:
        return self._request("GET", account_endpoint)

    def list_labels(self, labels_endpoint: str) -> list[dict[str, Any]]:
        response = self._request("GET", labels_endpoint)
        return normalize_label_records(response)

    def create_label(self, name: str, create_label_endpoint: str) -> dict[str, Any]:
        return self._request("POST", create_label_endpoint, json_body={"name": name})

    def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self.session.request(
                method,
                url,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            hint = network_error_hint(exc)
            raise ProofHubError(f"Network failure while calling {method} {url}: {exc}{hint}") from exc

        if response.status_code >= 400:
            raise ProofHubError(
                f"ProofHub API returned HTTP {response.status_code} for {method} {url}",
                response,
            )

        if not response.content:
            return {"ok": True}

        try:
            return response.json()
        except ValueError:
            return {"ok": True, "raw_response": response.text}


def _safe_response_text(response: requests.Response | None) -> str:
    if response is None:
        return ""
    text = response.text[:2000]
    return re.sub(r"(?i)(api[-_ ]?key|token|authorization)[\"':=\s]+[^,\s\"']+", r"\1=[redacted]", text)


def network_error_hint(exc: requests.RequestException) -> str:
    message = str(exc)
    if "WinError 10013" in message:
        return (
            "\n\nHint: Windows blocked the outbound socket before ProofHub received the request. "
            "Allow Python/Streamlit through Windows Defender Firewall, check VPN/proxy/endpoint security, "
            "or run the app from a network that permits outbound HTTPS to the configured ProofHub host."
        )
    if "Failed to establish a new connection" in message:
        return (
            "\n\nHint: The app could not reach the host. Check the API base URL, network connectivity, "
            "VPN/proxy settings, and firewall rules."
        )
    return ""


def extract_account_name(response: dict[str, Any]) -> str | None:
    priority_keys = (
        "account_name",
        "company_name",
        "organization_name",
        "workspace_name",
        "name",
        "company",
        "account",
        "subdomain",
        "email",
    )
    return find_named_value(response, priority_keys)


def find_named_value(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        normalized = {str(key).lower(): item for key, item in value.items()}
        for key in keys:
            item = normalized.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                nested = find_named_value(item, keys)
                if nested:
                    return nested
        for item in value.values():
            nested = find_named_value(item, keys)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = find_named_value(item, keys)
            if nested:
                return nested
    return None


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_relative_datetime(value: str, base: datetime | None = None) -> datetime | None:
    base = base or now_local()
    source = value.strip().lower()
    source = source.replace(",", " ")

    if not source:
        return None

    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", source)
    parsed_time = time(17, 0)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        suffix = time_match.group(3)
        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            parsed_time = time(hour, minute)

    target_date: date | None = None
    if "today" in source or "eod" in source or "end of day" in source:
        target_date = base.date()
    elif "tomorrow" in source:
        target_date = base.date() + timedelta(days=1)
    elif match := re.search(r"in\s+(\d+)\s+(day|days|week|weeks)", source):
        amount = int(match.group(1))
        days = amount * 7 if match.group(2).startswith("week") else amount
        target_date = base.date() + timedelta(days=days)
    elif match := re.search(r"next\s+(mon|tue|wed|thu|fri|sat|sun)\w*", source):
        weekdays = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        target = weekdays[match.group(1)]
        days_ahead = (target - base.weekday()) % 7
        target_date = base.date() + timedelta(days=days_ahead or 7)
    else:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%b %d %Y", "%B %d %Y"):
            try:
                target_date = datetime.strptime(value.strip(), fmt).date()
                break
            except ValueError:
                continue

    if target_date is None:
        return None
    return datetime.combine(target_date, parsed_time, tzinfo=base.tzinfo)


def split_blocks(raw_text: str) -> list[str]:
    normalized = raw_text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    structured_blocks = split_structured_task_blocks(normalized)
    if structured_blocks:
        return structured_blocks
    blocks = re.split(r"\n\s*\n+", normalized)
    if len(blocks) > 1:
        return attach_orphan_bullet_blocks([block.strip() for block in blocks if block.strip()])
    heading_blocks = re.split(r"\n(?=(?:create|new|update|edit|task|parent)\b)", normalized, flags=re.I)
    return attach_orphan_bullet_blocks([block.strip() for block in heading_blocks if block.strip()])


def split_structured_task_blocks(normalized: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"-{3,}", line):
            continue
        starts_task = bool(re.match(r"^(task\s*:|update\s+#?\d+)", line, flags=re.I))
        if starts_task and current:
            current_has_task = any(re.match(r"^(task\s*:|update\s+#?\d+)", existing, flags=re.I) for existing in current)
            if current_has_task:
                blocks.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    if len(blocks) == 1 and not re.match(r"^(task\s*:|update\s+#?\d+)", blocks[0], flags=re.I):
        return []
    return blocks


def attach_orphan_bullet_blocks(blocks: list[str]) -> list[str]:
    merged: list[str] = []
    for block in blocks:
        if merged and all(is_bullet_line(line) or not line.strip() for line in block.splitlines()):
            merged[-1] = f"{merged[-1]}\n{block}"
        else:
            merged.append(block)
    return merged


def is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^\s*[-*#_]+\s+\S", line))


def strip_bullet_marker(line: str) -> str:
    return re.sub(r"^\s*[-*#_]+\s+", "", line).strip()


def clean_title(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^[\s\-*#_]+", "", cleaned)
    cleaned = re.sub(r"[\s\-*#_]+$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def clean_field_value(value: str) -> str:
    return re.sub(r"\s{2,}", " ", value.strip().strip("*#-_ ")).strip()


INLINE_FIELD_LABELS = (
    "Start Date",
    "End Date",
    "Due Date",
    "Timeline",
    "Analyze Manual Workflow",
    "Define Extension Triggers",
    "Select Tech Stack",
    "Labels",
    "Assignees",
    "Time",
    "Progress",
    "Subtasks",
)


def extract_inline_fields(text: str, labels: tuple[str, ...] = INLINE_FIELD_LABELS) -> tuple[str, dict[str, str]]:
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    matches = list(re.finditer(rf"\b({label_pattern})\s*:", text, flags=re.I))
    if not matches:
        return text.strip(), {}

    title = text[: matches[0].start()].strip()
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).strip().lower().replace(" ", "_")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = clean_field_value(text[start:end])
        if value:
            fields[key] = value
    return title, fields


def parse_kv_lines(block: str) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    loose_lines: list[str] = []
    for line in block.splitlines():
        if is_bullet_line(line):
            loose_lines.append(strip_bullet_marker(line))
            continue
        clean = line.strip().strip("-")
        if not clean:
            continue
        if ":" in clean:
            key, value = clean.split(":", 1)
            normalized_key = key.strip().lower().replace(" ", "_")
            first_value, inline_fields = extract_inline_fields(value)
            fields[normalized_key] = clean_field_value(first_value)
            fields.update(inline_fields)
        else:
            loose_lines.append(clean)
    return fields, loose_lines


def parse_task_block(block: str, defaults: dict[str, Any], inherited_parent_id: str | None = None) -> ParsedTask:
    fields, loose_lines = parse_kv_lines(block)
    first_line = loose_lines[0] if loose_lines else block.splitlines()[0].strip()
    update_match = re.search(r"\b(?:update|edit|task)\s*#?(\d+)\b", first_line, flags=re.I)
    task_id = fields.get("task_id") or fields.get("id") or (update_match.group(1) if update_match else None)
    action: ActionKind = "update" if task_id else "create"

    title = (
        fields.get("title")
        or fields.get("task")
        or fields.get("parent")
        or re.sub(r"^(create|new|update|edit|task)\s*#?\d*:?\s*", "", first_line, flags=re.I).strip()
    )
    title = clean_title(title) or "Untitled task"

    subtasks = parse_subtasks(block, defaults)
    subtask_raw_lines = {subtask.raw for subtask in subtasks}
    description_parts = []
    for key in ("description", "notes", "details"):
        if fields.get(key):
            description_parts.append(fields[key])
    for label, key in (
        ("Timeline", "timeline"),
        ("Manual workflow", "analyze_manual_workflow"),
        ("Extension triggers", "define_extension_triggers"),
        ("Tech stack", "select_tech_stack"),
        ("Time estimate", "time"),
        ("Progress", "progress"),
        ("End date", "end_date"),
    ):
        if fields.get(key):
            description_parts.append(f"{label}: {fields[key]}")
    for line in loose_lines[1:]:
        if not line.lower().startswith(("subtask", "child")) and line not in subtask_raw_lines:
            description_parts.append(line)

    due_raw = fields.get("due") or fields.get("due_date") or fields.get("deadline") or fields.get("end_date")
    start_raw = fields.get("start") or fields.get("start_date")
    labels_raw = fields.get("labels") or fields.get("tags") or ""
    assignees_raw = fields.get("assignees") or fields.get("assigned_to") or fields.get("owners") or ""

    return ParsedTask(
        action=action,
        title=title,
        task_id=task_id,
        description="\n".join(description_parts).strip(),
        due_at=parse_relative_datetime(due_raw) if due_raw else None,
        start_at=parse_relative_datetime(start_raw) if start_raw else None,
        status=fields.get("status") or infer_status(first_line),
        priority=fields.get("priority"),
        labels=parse_csv(labels_raw),
        assignee_ids=parse_csv(assignees_raw),
        project_id=fields.get("project") or fields.get("project_id") or defaults.get("project_id"),
        tasklist_id=fields.get("tasklist") or fields.get("tasklist_id") or defaults.get("tasklist_id"),
        parent_id=fields.get("parent_id") or inherited_parent_id,
        subtasks=subtasks,
        raw=block,
        metadata={
            "source": "regex",
            "parsed_at": now_local().isoformat(),
            "source_fields": fields,
        },
    )


def parse_subtasks(block: str, defaults: dict[str, Any]) -> list[ParsedTask]:
    structured_subtasks = parse_structured_subtasks(block, defaults)
    if structured_subtasks:
        return structured_subtasks

    subtasks: list[ParsedTask] = []
    capture = False
    seen_content_line = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"\bsubtasks?\s*:\s*$", stripped, flags=re.I):
            capture = True
            seen_content_line = True
            continue
        if re.match(r"^(subtasks?|children)\s*:", stripped, flags=re.I):
            capture = True
            remainder = stripped.split(":", 1)[1].strip()
            if remainder:
                subtasks.append(task_from_subtask_line(remainder, defaults))
            seen_content_line = True
            continue
        if capture:
            if re.match(r"^(task\s*:|update\s+#?\d+)", stripped, flags=re.I):
                capture = False
                seen_content_line = True
                continue
            subtasks.append(task_from_subtask_line(strip_bullet_marker(stripped), defaults))
        elif seen_content_line and is_bullet_line(stripped):
            subtasks.append(task_from_subtask_line(strip_bullet_marker(stripped), defaults))
        seen_content_line = True
    return subtasks


def parse_structured_subtasks(block: str, defaults: dict[str, Any]) -> list[ParsedTask]:
    subtasks: list[ParsedTask] = []
    current: list[str] = []
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped or re.fullmatch(r"-{3,}", stripped):
            continue
        if re.match(r"^subtask\s*:", stripped, flags=re.I):
            if current:
                subtasks.append(parse_subtask_block("\n".join(current), defaults))
            current = [stripped]
            continue
        if current:
            if re.match(r"^(task\s*:|update\s+#?\d+)", stripped, flags=re.I):
                break
            current.append(stripped)
    if current:
        subtasks.append(parse_subtask_block("\n".join(current), defaults))
    return subtasks


def parse_subtask_block(block: str, defaults: dict[str, Any]) -> ParsedTask:
    fields, loose_lines = parse_kv_lines(block)
    first_line = loose_lines[0] if loose_lines else block.splitlines()[0].strip()
    title = fields.get("subtask") or fields.get("task") or re.sub(r"^subtask\s*:?\s*", "", first_line, flags=re.I).strip()
    due_raw = fields.get("due") or fields.get("due_date") or fields.get("deadline") or fields.get("end_date")
    start_raw = fields.get("start") or fields.get("start_date")
    description_parts = [fields[key] for key in ("description", "notes", "details") if fields.get(key)]
    return ParsedTask(
        action="create",
        title=clean_title(title) or "Untitled subtask",
        description="\n".join(description_parts).strip(),
        due_at=parse_relative_datetime(due_raw) if due_raw else None,
        start_at=parse_relative_datetime(start_raw) if start_raw else None,
        status=fields.get("status") or infer_status(first_line),
        priority=fields.get("priority"),
        labels=parse_csv(fields.get("labels") or fields.get("tags") or ""),
        assignee_ids=parse_csv(fields.get("assignees") or fields.get("assigned_to") or fields.get("owners") or ""),
        project_id=None,
        tasklist_id=None,
        raw=block,
        metadata={"source": "regex-subtask-block", "parsed_at": now_local().isoformat(), "source_fields": fields},
    )


def task_from_subtask_line(line: str, defaults: dict[str, Any]) -> ParsedTask:
    due_at = None
    start_at = None
    status = None
    priority = None
    title, fields = extract_inline_fields(
        line,
        labels=("start", "end", "due", "status", "priority", "Start Date", "End Date", "Due Date"),
    )
    line = title or line

    start_raw = fields.get("start") or fields.get("start_date")
    due_raw = fields.get("due") or fields.get("due_date") or fields.get("end") or fields.get("end_date")
    if start_raw:
        start_at = parse_relative_datetime(start_raw)
    if due_raw:
        due_at = parse_relative_datetime(due_raw)
    status = fields.get("status")
    priority = fields.get("priority")

    if due_at is None and (match := re.search(r"\bdue\s+([^;|]+)", line, flags=re.I)):
        due_at = parse_relative_datetime(match.group(1).strip())
        line = line[: match.start()].strip(" -;|")
    if status is None and (match := re.search(r"\bstatus\s*[:=]\s*([^;|]+)", line, flags=re.I)):
        status = match.group(1).strip()
        line = line[: match.start()].strip(" -;|")
    if priority is None and (match := re.search(r"\bpriority\s*[:=]\s*([^;|]+)", line, flags=re.I)):
        priority = match.group(1).strip()
        line = line[: match.start()].strip(" -;|")

    return ParsedTask(
        action="create",
        title=clean_title(line) or "Untitled subtask",
        due_at=due_at,
        start_at=start_at,
        status=status,
        priority=priority,
        project_id=None,
        tasklist_id=None,
        raw=line,
        metadata={"source": "regex-subtask", "parsed_at": now_local().isoformat()},
    )


def parse_csv(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]


def infer_status(text: str) -> str | None:
    lowered = text.lower()
    if any(term in lowered for term in ("complete", "done", "finished")):
        return "Completed"
    if any(term in lowered for term in ("blocked", "waiting")):
        return "Blocked"
    if any(term in lowered for term in ("start", "doing", "progress")):
        return "In Progress"
    return None


def parse_input(raw_text: str, defaults: dict[str, Any]) -> ParseResult:
    warnings: list[str] = []
    tasks = [parse_task_block(block, defaults) for block in split_blocks(raw_text)]

    for task in tasks:
        if task.action == "create" and not task.tasklist_id:
            warnings.append(f"`{task.title}` is missing a tasklist ID.")
        if not task.project_id:
            warnings.append(f"`{task.title}` is missing a project ID.")
        if task.action == "update" and not task.task_id:
            warnings.append(f"`{task.title}` looks like an update but has no task ID.")

    return ParseResult(tasks=tasks, warnings=warnings, defaults=defaults)


def build_payload(
    task: ParsedTask,
    status_map: dict[str, str],
    label_map: dict[str, int] | None = None,
    inferred_labels: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": task.title,
    }
    if task.description:
        payload["description"] = task.description
    if task.due_at:
        payload["due_date"] = task.due_at.date().isoformat()
    if task.start_at:
        payload["start_date"] = task.start_at.date().isoformat()
    numeric_labels = resolve_label_ids(task.labels + (inferred_labels or []), label_map or {})
    if numeric_labels:
        payload["labels"] = numeric_labels
    numeric_assignees = numeric_ids(task.assignee_ids)
    if numeric_assignees:
        payload["assigned"] = numeric_assignees
    if task.status:
        lowered_status = status_map.get(task.status.lower(), task.status).lower()
        if lowered_status in {"done", "complete", "completed"}:
            payload["completed"] = True
        elif lowered_status in {"todo", "to do", "in progress", "blocked"}:
            payload["completed"] = False
    return payload


def resolve_label_ids(values: list[str], label_map: dict[str, int]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        clean = value.strip()
        if not clean:
            continue
        label_id = int(clean) if clean.isdigit() else label_map.get(clean.lower())
        if label_id and label_id not in seen:
            ids.append(label_id)
            seen.add(label_id)
    return ids


def numeric_ids(values: list[str]) -> list[int]:
    ids: list[int] = []
    for value in values:
        clean = value.strip()
        if clean.isdigit():
            ids.append(int(clean))
    return ids


def parse_bucket_map(raw_map: str, default_tasklist_id: str) -> dict[str, str]:
    buckets: dict[str, str] = {"default": default_tasklist_id}
    for line in raw_map.splitlines():
        clean = line.strip()
        if not clean or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            buckets[key] = value
    return buckets


def parse_label_map(raw_map: str) -> dict[str, int]:
    labels: dict[str, int] = {}
    for line in raw_map.splitlines():
        clean = line.strip()
        if not clean or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value.isdigit():
            labels[key] = int(value)
    return labels


def normalize_label_records(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if not isinstance(response, dict):
        return []

    for key in ("data", "labels", "items", "results"):
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = normalize_label_records(value)
            if nested:
                return nested

    if response.get("name") and (response.get("id") or response.get("label_id")):
        return [response]
    return []


def normalize_task_records(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if not isinstance(response, dict):
        return []

    for key in ("data", "tasks", "items", "results"):
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = normalize_task_records(value)
            if nested:
                return nested

    if (response.get("title") or response.get("name")) and (response.get("id") or response.get("task_id")):
        return [response]
    return []


def normalized_title(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def task_record_title(record: dict[str, Any]) -> str:
    return str(record.get("title") or record.get("name") or record.get("task_title") or "").strip()


def task_record_id(record: dict[str, Any]) -> str | None:
    task_id = record.get("id") or record.get("task_id")
    return str(task_id) if task_id else None


def existing_subtasks_by_title(
    client: ProofHubClient,
    project_id: str,
    tasklist_id: str,
    parent_id: str,
    list_subtasks_endpoint: str,
) -> dict[str, str]:
    subtasks = client.list_subtasks(project_id, tasklist_id, parent_id, list_subtasks_endpoint)
    matches: dict[str, str] = {}
    for record in subtasks:
        title = normalized_title(task_record_title(record))
        task_id = task_record_id(record)
        if title and task_id and title not in matches:
            matches[title] = task_id
    return matches


def label_map_from_records(records: Any) -> dict[str, int]:
    if not isinstance(records, list):
        records = normalize_label_records(records)
    labels: dict[str, int] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or record.get("title") or "").strip()
        label_id = record.get("id") or record.get("label_id")
        if name and str(label_id).isdigit():
            labels[name.lower()] = int(label_id)
    return labels


def label_map_text(label_map: dict[str, int]) -> str:
    return "\n".join(f"{name}={label_id}" for name, label_id in sorted(label_map.items()))


def effective_label_map(configured_label_map: dict[str, int]) -> dict[str, int]:
    fetch_result = st.session_state.get("label_fetch_result")
    fetched = fetch_result.get("labels", {}) if isinstance(fetch_result, dict) and fetch_result.get("level") == "success" else {}
    return {**fetched, **configured_label_map}


def normalize_words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def task_context(task: ParsedTask) -> str:
    parts = [task.title, task.description, task.status or "", " ".join(task.labels)]
    parts.extend(subtask.title for subtask in task.subtasks)
    return " ".join(parts).lower()


SEMANTIC_ROUTES = [
    (("frontend", "front-end", "ui", "ux", "interface", "layout", "design system", "styling"), ("ui/ux", "frontend")),
    (("backend", "api", "database", "postgres", "redis", "server", "worker", "pipeline"), ("backend",)),
    (("qa", "test", "testing", "validation", "bug", "approval"), ("qa", "testing")),
    (("security", "auth", "permission", "cryptographic", "isolation"), ("security",)),
    (("content", "eeat", "seo", "keyword", "metadata", "search console"), ("seo", "content")),
    (("voice", "call", "outbound", "webrtc", "demo"), ("voice", "operations")),
    (("deployment", "docker", "release", "shipping", "infrastructure"), ("deployment", "infrastructure")),
]


def infer_task_labels(task: ParsedTask) -> list[str]:
    if str(task.metadata.get("source", "")).startswith("regex-subtask"):
        return []
    if task.labels:
        return [task.priority.lower()] if task.priority else []

    context = task_context(task)
    labels: list[str] = []
    for keywords, label_names in SEMANTIC_ROUTES:
        if any(keyword in context for keyword in keywords):
            labels.append(label_names[0])
    if task.priority:
        labels.append(task.priority.lower())
    return labels


def labels_needed_for_parse_result(parse_result: ParseResult) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for task in parse_result.tasks:
        for label in task.labels + infer_task_labels(task):
            clean = label.strip()
            if clean and not clean.isdigit() and clean.lower() not in seen:
                labels.append(clean)
                seen.add(clean.lower())
        for subtask in task.subtasks:
            for label in subtask.labels + infer_task_labels(subtask):
                clean = label.strip()
                if clean and not clean.isdigit() and clean.lower() not in seen:
                    labels.append(clean)
                    seen.add(clean.lower())
    return labels


def label_coverage(parse_result: ParseResult, label_map: dict[str, int]) -> tuple[list[str], list[str]]:
    needed = labels_needed_for_parse_result(parse_result)
    mapped = [label for label in needed if label.lower() in label_map]
    missing = [label for label in needed if label.lower() not in label_map]
    return mapped, missing


def sync_label_map(
    client: ProofHubClient,
    label_map: dict[str, int],
    needed_labels: list[str],
    labels_endpoint: str,
    create_label_endpoint: str,
    auto_create_missing_labels: bool,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    logs: list[dict[str, Any]] = []
    merged = dict(label_map)
    missing = [label for label in needed_labels if label.lower() not in merged]
    if not missing:
        return merged, logs

    try:
        merged.update(label_map_from_records(client.list_labels(labels_endpoint)))
    except ProofHubError as exc:
        logs.append(error_log("Labels", str(exc), exc.status_code, exc.body))
        return merged, logs

    for label_name in missing:
        if label_name.lower() in merged:
            continue
        if not auto_create_missing_labels:
            logs.append(
                {
                    "time": now_local().strftime("%H:%M:%S"),
                    "level": "info",
                    "message": f'Label "{label_name}" inferred but not added because no ProofHub label ID exists in the Label map.',
                }
            )
            continue
        try:
            response = client.create_label(label_name, create_label_endpoint)
            label_id = extract_label_id(response)
            if label_id:
                merged[label_name.lower()] = int(label_id)
                logs.append(label_success_log(label_name, label_id, response))
            else:
                try:
                    merged.update(label_map_from_records(client.list_labels(labels_endpoint)))
                except ProofHubError:
                    pass
                if label_name.lower() in merged:
                    logs.append(label_success_log(label_name, str(merged[label_name.lower()]), response))
                else:
                    logs.append(
                        error_log(
                            label_name,
                            "ProofHub did not return a label ID after creating the label. Use Fetch Labels or paste the label ID in Label map.",
                            None,
                            "",
                        )
                    )
        except ProofHubError as exc:
            logs.append(error_log(label_name, str(exc), exc.status_code, exc.body))
    return merged, logs


def is_standalone_scope(task: ParsedTask, defaults: dict[str, Any] | None = None) -> bool:
    defaults = defaults or {}
    if task.project_id and task.tasklist_id and (
        task.project_id != defaults.get("project_id") or task.tasklist_id != defaults.get("tasklist_id")
    ):
        return False
    context = task_context(task)
    title_context = task.title.lower()
    explicit_new_scope_terms = (
        "new mini-app",
        "new mini app",
        "new web scraper",
        "new automation tool",
        "standalone mini-app",
        "standalone mini app",
        "standalone automation",
        "separate project",
        "separate tool",
    )
    if any(term in title_context for term in explicit_new_scope_terms):
        return True
    return bool(
        re.search(
            r"\b(?:new|standalone|separate)\b.*\b(?:app|tool|scraper|extension|portal|dashboard|platform|product|project)\b",
            title_context,
            flags=re.I,
        )
    )


def likely_daily_update(task: ParsedTask, known_titles: list[str]) -> tuple[bool, str | None]:
    if task.task_id:
        return True, task.task_id
    context = task_context(task)
    continuity_terms = (
        "daily update",
        "status update",
        "progress",
        "continued",
        "continuing",
        "follow up",
        "follow-up",
        "reviewed",
        "blocked",
        "completed",
        "done",
        "today",
        "yesterday",
        "tomorrow",
    )
    if not any(term in context for term in continuity_terms):
        return False, None

    task_words = normalize_words(task.title)
    for known_title in known_titles:
        known_words = normalize_words(known_title)
        if not known_words:
            continue
        overlap = len(task_words & known_words) / max(1, min(len(task_words), len(known_words)))
        if overlap >= 0.6:
            return True, known_title
    return False, None


def choose_bucket(task: ParsedTask, bucket_map: dict[str, str]) -> tuple[str, str]:
    context = task_context(task)

    for keywords, bucket_names in SEMANTIC_ROUTES:
        if any(keyword in context for keyword in keywords):
            for bucket_name in bucket_names:
                if bucket_name in bucket_map:
                    return bucket_map[bucket_name], f"matched `{bucket_name}` context keywords"
    return bucket_map.get("default", task.tasklist_id or DEFAULT_TASKLIST_ID), "fell back to default active bucket"


def initial_project_roadmap(task: ParsedTask) -> list[dict[str, str]]:
    return [
        {"title": "Discovery", "description": "Clarify scope, users, inputs, outputs, and success criteria."},
        {"title": "Build", "description": "Implement the core workflow, integrations, and UI needed for a working first version."},
        {"title": "QA & Launch", "description": "Validate behavior, fix defects, document usage, and prepare release."},
    ]


def route_tasks(
    parse_result: ParseResult,
    status_map: dict[str, str],
    bucket_map: dict[str, str],
    label_map: dict[str, int],
    known_titles: list[str],
) -> list[RoutingDecision]:
    decisions: list[RoutingDecision] = []
    for task in parse_result.tasks:
        inferred_labels = infer_task_labels(task)
        payload = build_payload(task, status_map, label_map, inferred_labels)
        payload.setdefault("title", task.title)
        payload.setdefault("description", task.description)
        payload["status"] = task.status or ("done" if payload.get("completed") else "todo")
        payload.setdefault("start_date", task.start_at.date().isoformat() if task.start_at else None)
        payload.setdefault("due_date", task.due_at.date().isoformat() if task.due_at else None)
        payload["inferred_labels"] = inferred_labels

        if is_standalone_scope(task, parse_result.defaults):
            payload["roadmap_tasklists"] = initial_project_roadmap(task)
            decisions.append(
                RoutingDecision(
                    action_type="create_project",
                    target_bucket_id=None,
                    task_payload=payload,
                    routing_justification="This request describes a standalone product/tool scope, so it should be isolated as a new ProofHub project.",
                )
            )
            continue

        is_update, match = likely_daily_update(task, known_titles)
        if is_update:
            target_bucket_id, reason = choose_bucket(task, bucket_map)
            task.tasklist_id = target_bucket_id
            decisions.append(
                RoutingDecision(
                    action_type="update_existing",
                    target_bucket_id=target_bucket_id,
                    task_payload=payload,
                    routing_justification=f"Daily continuity terms matched existing work `{match or task.task_id}`, so this should update/append instead of duplicating a parent task.",
                )
            )
            continue

        target_bucket_id, reason = choose_bucket(task, bucket_map)
        task.tasklist_id = target_bucket_id
        decisions.append(
            RoutingDecision(
                action_type="create_task",
                target_bucket_id=target_bucket_id,
                task_payload=payload,
                routing_justification=f"Routed to bucket `{target_bucket_id}` because it {reason}.",
            )
        )
    return decisions


def routing_decisions_json(decisions: list[RoutingDecision]) -> list[dict[str, Any]]:
    return [
        {
            "action_type": decision.action_type,
            "target_bucket_id": decision.target_bucket_id,
            "task_payload": decision.task_payload,
            "routing_justification": decision.routing_justification,
        }
        for decision in decisions
    ]


def parse_status_map(raw_map: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in raw_map.splitlines():
        if not line.strip() or "=" not in line:
            continue
        key, value = line.split("=", 1)
        mapping[key.strip().lower()] = value.strip()
    return mapping


def flatten_preview(tasks: list[ParsedTask], status_map: dict[str, str], label_map: dict[str, int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        rows.append(task_to_row(task, status_map, label_map, "parent"))
        for subtask in task.subtasks:
            rows.append(
                task_to_row(
                    subtask,
                    status_map,
                    label_map,
                    "subtask",
                    inherited_project_id=task.project_id,
                    inherited_tasklist_id=task.tasklist_id,
                )
            )
    return rows


def task_to_row(
    task: ParsedTask,
    status_map: dict[str, str],
    label_map: dict[str, int],
    level: str,
    inherited_project_id: str | None = None,
    inherited_tasklist_id: str | None = None,
) -> dict[str, Any]:
    inferred_labels = infer_task_labels(task)
    payload = build_payload(task, status_map, label_map, inferred_labels)
    project_id = task.project_id or inherited_project_id or ""
    tasklist_id = task.tasklist_id or inherited_tasklist_id or ""
    return {
        "level": level,
        "action": task.action,
        "task_id": task.task_id or "",
        "title": task.title,
        "project_id": project_id,
        "tasklist_id": tasklist_id,
        "parent_id": task.parent_id or "",
        "status": task.status or "",
        "labels": ", ".join(task.labels + inferred_labels),
        "due": payload.get("due_at", payload.get("due_date", "")),
        "payload": payload,
    }


def validate_execution(parse_result: ParseResult) -> list[str]:
    errors: list[str] = []
    for task in parse_result.tasks:
        if not task.title:
            errors.append("A parsed task has no title.")
        if not task.project_id:
            errors.append(f"`{task.title}` cannot run without a project ID.")
        if task.action == "create" and not task.tasklist_id:
            errors.append(f"`{task.title}` cannot be created without a tasklist ID.")
        if task.action == "update":
            if not task.tasklist_id:
                errors.append(f"`{task.title}` cannot be updated without a tasklist ID.")
            if not task.task_id:
                errors.append(f"`{task.title}` cannot be updated without a task ID.")
        for subtask in task.subtasks:
            project_id = subtask.project_id or task.project_id
            tasklist_id = subtask.tasklist_id or task.tasklist_id
            if not project_id:
                errors.append(f"`{subtask.title}` cannot run without a project ID.")
            if subtask.action == "create" and not tasklist_id:
                errors.append(f"`{subtask.title}` cannot be created without a tasklist ID.")
            if subtask.action == "update" and not subtask.task_id:
                errors.append(f"`{subtask.title}` cannot be updated without a task ID.")
    return errors


def execute_tasks(
    client: ProofHubClient,
    parse_result: ParseResult,
    status_map: dict[str, str],
    label_map: dict[str, int],
    create_endpoint: str,
    create_subtask_endpoint: str,
    update_endpoint: str,
    skip_matching_subtasks: bool = True,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for task in parse_result.tasks:
        try:
            payload = build_payload(task, status_map, label_map, infer_task_labels(task))
            if task.action == "create":
                assert task.project_id and task.tasklist_id
                response = client.create_task(task.project_id, task.tasklist_id, payload, create_endpoint)
                created_id = extract_task_id(response)
                logs.append(success_log("created_task", task.title, response, task.project_id, task.tasklist_id))
                existing_subtasks: dict[str, str] = {}
                if skip_matching_subtasks and created_id:
                    existing_subtasks = load_existing_subtasks_for_parent(
                        client,
                        task.project_id,
                        task.tasklist_id,
                        created_id,
                        create_subtask_endpoint,
                        logs,
                    )
                for subtask in task.subtasks:
                    subtask.project_id = subtask.project_id or task.project_id
                    subtask.tasklist_id = subtask.tasklist_id or task.tasklist_id
                    subtask.parent_id = subtask.parent_id or created_id
                    sub_payload = build_payload(subtask, status_map, label_map, infer_task_labels(subtask))
                    if not subtask.parent_id:
                        logs.append(error_log(subtask.title, "Parent task ID was not present in the create response.", None, ""))
                        continue
                    existing_subtask_id = existing_subtasks.get(normalized_title(subtask.title))
                    if existing_subtask_id:
                        logs.append(skipped_subtask_log(subtask.title, task.title, existing_subtask_id))
                        continue
                    response = client.create_subtask(
                        subtask.project_id,
                        subtask.tasklist_id,
                        subtask.parent_id,
                        sub_payload,
                        create_subtask_endpoint,
                    )
                    logs.append(
                        success_log(
                            "created_subtask",
                            subtask.title,
                            response,
                            subtask.project_id,
                            subtask.tasklist_id,
                            parent_title=task.title,
                        )
                    )
            else:
                assert task.project_id and task.tasklist_id and task.task_id
                response = client.update_task(
                    task.project_id,
                    task.tasklist_id,
                    task.task_id,
                    payload,
                    update_endpoint,
                )
                logs.append(success_log("updated_task", task.title, response, task.project_id, task.tasklist_id))
                existing_subtasks: dict[str, str] = {}
                if skip_matching_subtasks:
                    existing_subtasks = load_existing_subtasks_for_parent(
                        client,
                        task.project_id,
                        task.tasklist_id,
                        task.task_id,
                        create_subtask_endpoint,
                        logs,
                    )
                for subtask in task.subtasks:
                    subtask.project_id = subtask.project_id or task.project_id
                    subtask.tasklist_id = subtask.tasklist_id or task.tasklist_id
                    subtask.parent_id = subtask.parent_id or task.task_id
                    sub_payload = build_payload(subtask, status_map, label_map, infer_task_labels(subtask))
                    existing_subtask_id = existing_subtasks.get(normalized_title(subtask.title))
                    if existing_subtask_id:
                        logs.append(skipped_subtask_log(subtask.title, task.title, existing_subtask_id))
                        continue
                    response = client.create_subtask(
                        subtask.project_id,
                        subtask.tasklist_id,
                        subtask.parent_id,
                        sub_payload,
                        create_subtask_endpoint,
                    )
                    logs.append(
                        success_log(
                            "created_subtask",
                            subtask.title,
                            response,
                            subtask.project_id,
                            subtask.tasklist_id,
                            parent_title=task.title,
                        )
                    )
        except ProofHubError as exc:
            logs.append(error_log(task.title, str(exc), exc.status_code, exc.body))
        except Exception as exc:
            logs.append(error_log(task.title, f"Unexpected execution error: {exc}", None, ""))
    return logs


def apply_existing_task_matches(
    client: ProofHubClient,
    parse_result: ParseResult,
    routing_decisions: list[RoutingDecision],
    list_tasks_endpoint: str,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    buckets: dict[tuple[str, str], list[ParsedTask]] = {}
    for task, decision in zip(parse_result.tasks, routing_decisions):
        if decision.action_type != "create_task" or task.action != "create" or not task.project_id or not task.tasklist_id:
            continue
        buckets.setdefault((task.project_id, task.tasklist_id), []).append(task)

    for (project_id, tasklist_id), tasks in buckets.items():
        try:
            existing_records = client.list_tasks(project_id, tasklist_id, list_tasks_endpoint)
        except ProofHubError as exc:
            logs.append(error_log("Existing task lookup", str(exc), exc.status_code, exc.body))
            continue

        existing_by_title: dict[str, str] = {}
        for record in existing_records:
            title = normalized_title(task_record_title(record))
            task_id = task_record_id(record)
            if title and task_id and title not in existing_by_title:
                existing_by_title[title] = task_id

        for task in tasks:
            task_id = existing_by_title.get(normalized_title(task.title))
            if not task_id:
                continue
            task.action = "update"
            task.task_id = task_id
            logs.append(
                {
                    "time": now_local().strftime("%H:%M:%S"),
                    "level": "info",
                    "message": f'Existing task matched: "{task.title}" will be updated instead of duplicated (task ID {task_id}).',
                }
            )
    return logs


def load_existing_subtasks_for_parent(
    client: ProofHubClient,
    project_id: str,
    tasklist_id: str,
    parent_id: str,
    list_subtasks_endpoint: str,
    logs: list[dict[str, Any]],
) -> dict[str, str]:
    try:
        return existing_subtasks_by_title(client, project_id, tasklist_id, parent_id, list_subtasks_endpoint)
    except ProofHubError as exc:
        logs.append(error_log("Existing subtask lookup", str(exc), exc.status_code, exc.body))
        return {}


def execute_project_commands(
    client: ProofHubClient,
    parse_result: ParseResult,
    routing_decisions: list[RoutingDecision],
    create_project_endpoint: str,
    create_tasklist_endpoint: str,
    create_roadmap_tasklists: bool,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for task, decision in zip(parse_result.tasks, routing_decisions):
        if decision.action_type != "create_project":
            continue
        try:
            project_payload = project_payload_from_task(task)
            response = client.create_project(project_payload, create_project_endpoint)
            logs.append(project_success_log(task.title, response))
            project_id = extract_project_id(response)
            if create_roadmap_tasklists and project_id:
                for tasklist in initial_project_roadmap(task):
                    try:
                        tasklist_response = client.create_tasklist(project_id, tasklist, create_tasklist_endpoint)
                        logs.append(tasklist_success_log(tasklist["title"], task.title, tasklist_response, project_id))
                    except ProofHubError as exc:
                        logs.append(error_log(tasklist["title"], str(exc), exc.status_code, exc.body))
            elif create_roadmap_tasklists:
                logs.append(error_log(task.title, "Project ID was not present in the create project response, so roadmap tasklists were not created.", None, ""))
        except ProofHubError as exc:
            logs.append(error_log(task.title, str(exc), exc.status_code, exc.body))
        except Exception as exc:
            logs.append(error_log(task.title, f"Unexpected project creation error: {exc}", None, ""))
    return logs


def project_payload_from_task(task: ParsedTask) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": task.title,
        "description": task.description or f"Auto-created from ProofHub Task Orchestrator on {now_local().date().isoformat()}.",
    }
    if task.start_at:
        payload["start_date"] = task.start_at.date().isoformat()
    if task.due_at:
        payload["end_date"] = task.due_at.date().isoformat()
    assignees = numeric_ids(task.assignee_ids)
    if assignees:
        payload["assigned"] = assignees
        payload["manager"] = assignees[0]
    return payload


def prepare_executable_parse_result(
    parse_result: ParseResult,
    routing_decisions: list[RoutingDecision],
    create_standalone_projects: bool,
) -> tuple[ParseResult, list[dict[str, Any]]]:
    executable_tasks: list[ParsedTask] = []
    route_logs: list[dict[str, Any]] = []
    for task, decision in zip(parse_result.tasks, routing_decisions):
        if decision.action_type == "create_project":
            if not create_standalone_projects:
                route_logs.append(
                    {
                        "time": now_local().strftime("%H:%M:%S"),
                        "level": "info",
                        "message": f'Project creation recommended: "{task.title}" should be created as a separate ProofHub project with its own roadmap.',
                        "response": routing_decisions_json([decision])[0],
                    }
                )
            continue
        if decision.action_type == "update_existing":
            if not task.task_id:
                route_logs.append(
                    error_log(
                        task.title,
                        "Routed as update_existing but no ProofHub task ID was supplied. Add `Update #TASK_ID` or review manually.",
                        None,
                        "",
                    )
                )
                continue
            task.action = "update"
        executable_tasks.append(task)
    return ParseResult(tasks=executable_tasks, warnings=parse_result.warnings, defaults=parse_result.defaults), route_logs


def extract_task_id(response: dict[str, Any]) -> str | None:
    for key in ("id", "task_id"):
        if key in response:
            return str(response[key])
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("id", "task_id"):
            if key in data:
                return str(data[key])
    return None


def extract_project_id(response: dict[str, Any]) -> str | None:
    for key in ("id", "project_id"):
        if key in response:
            return str(response[key])
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("id", "project_id"):
            if key in data:
                return str(data[key])
    return None


def extract_label_id(response: dict[str, Any]) -> str | None:
    for key in ("id", "label_id"):
        if key in response:
            return str(response[key])
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("id", "label_id"):
            if key in data:
                return str(data[key])
    return None


def response_data(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    return data if isinstance(data, dict) else response


def nested_name(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            for name_key in ("name", "title"):
                name = value.get(name_key)
                if name:
                    return str(name)
        elif value:
            return str(value)
    return None


def proofhub_result_message(
    action: str,
    title: str,
    response: dict[str, Any],
    project_id: str | None = None,
    tasklist_id: str | None = None,
    parent_title: str | None = None,
) -> str:
    data = response_data(response)
    project_name = nested_name(data, "project", "project_name") or (f"project {project_id}" if project_id else "the active project")
    tasklist_name = nested_name(data, "list", "tasklist", "todolist", "tasklist_name") or (
        f"tasklist {tasklist_id}" if tasklist_id else "the selected tasklist"
    )
    task_id = extract_task_id(response)
    task_suffix = f" (task ID {task_id})" if task_id else ""

    if action == "created_subtask":
        parent = f' under "{parent_title}"' if parent_title else ""
        return f'Project "{project_name}" updated: added subtask "{title}"{parent} in "{tasklist_name}"{task_suffix}.'
    if action == "updated_task":
        return f'Project "{project_name}" updated: updated task "{title}" in "{tasklist_name}"{task_suffix}.'
    return f'Project "{project_name}" updated: created task "{title}" in "{tasklist_name}"{task_suffix}.'


def success_log(
    action: str,
    title: str,
    response: dict[str, Any],
    project_id: str | None = None,
    tasklist_id: str | None = None,
    parent_title: str | None = None,
) -> dict[str, Any]:
    return {
        "time": now_local().strftime("%H:%M:%S"),
        "level": "success",
        "message": proofhub_result_message(action, title, response, project_id, tasklist_id, parent_title),
        "response": response,
    }


def project_success_log(title: str, response: dict[str, Any]) -> dict[str, Any]:
    project_id = extract_project_id(response)
    suffix = f" (project ID {project_id})" if project_id else ""
    return {
        "time": now_local().strftime("%H:%M:%S"),
        "level": "success",
        "message": f'Project "{title}" created successfully{suffix}.',
        "response": response,
    }


def tasklist_success_log(title: str, project_title: str, response: dict[str, Any], project_id: str) -> dict[str, Any]:
    data = response_data(response)
    tasklist_id = data.get("id") or data.get("tasklist_id")
    suffix = f" (tasklist ID {tasklist_id})" if tasklist_id else ""
    return {
        "time": now_local().strftime("%H:%M:%S"),
        "level": "success",
        "message": f'Project "{project_title}" updated: created roadmap tasklist "{title}" in project {project_id}{suffix}.',
        "response": response,
    }


def label_success_log(title: str, label_id: str, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": now_local().strftime("%H:%M:%S"),
        "level": "success",
        "message": f'Label "{title}" is ready in ProofHub (label ID {label_id}).',
        "response": response,
    }


def skipped_subtask_log(title: str, parent_title: str, subtask_id: str) -> dict[str, Any]:
    return {
        "time": now_local().strftime("%H:%M:%S"),
        "level": "info",
        "message": f'Existing subtask matched: "{title}" already exists under "{parent_title}" (subtask ID {subtask_id}); skipped duplicate creation.',
    }


def error_log(title: str, message: str, status_code: int | None, body: str) -> dict[str, Any]:
    return {
        "time": now_local().strftime("%H:%M:%S"),
        "level": "error",
        "message": f"{title}: {message}",
        "status_code": status_code,
        "body": body,
    }


def task_summary(task: ParsedTask) -> str:
    details = [task.action.upper()]
    if task.task_id:
        details.append(f"#{task.task_id}")
    if task.status:
        details.append(f"status={task.status}")
    if task.due_at:
        details.append(f"due={task.due_at.strftime('%Y-%m-%d %H:%M')}")
    if task.subtasks:
        details.append(f"{len(task.subtasks)} subtasks")
    return " - ".join(details)


def render_chat_message(role: str, text: str) -> None:
    with st.chat_message(role):
        st.markdown(text)


def init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("raw_text", "")
    st.session_state.setdefault("run_logs", [])
    st.session_state.setdefault("connection_result", None)
    st.session_state.setdefault("label_fetch_result", None)


def load_text_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def sample_template_text() -> str:
    try:
        return load_text_file(DEFAULT_PROVIDED_TASK_FILE)
    except OSError:
        return DEFAULT_SAMPLE_TEMPLATE


def render_copy_sample_button(template: str) -> None:
    render_copy_button("copy-sample-template", "Copy Sample Text", "Copied Template", template)


def render_copy_button(element_id: str, label: str, copied_label: str, text: str) -> None:
    button_html = f"""
    <button id="{element_id}" style="
        width: 100%;
        min-height: 2.7rem;
        border: 1px solid rgba(255,255,255,0.18);
        border-radius: 8px;
        background: rgba(255,255,255,0.05);
        color: #f4f4f0;
        font: 600 0.92rem/1.2 system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        cursor: pointer;
    ">{label}</button>
    <script>
    const button = document.getElementById("{element_id}");
    const template = {json.dumps(text)};
    button.addEventListener("click", async () => {{
        try {{
            await navigator.clipboard.writeText(template);
            button.textContent = "{copied_label}";
        }} catch (error) {{
            button.textContent = "Copy Failed";
        }}
        setTimeout(() => button.textContent = "{label}", 1800);
    }});
    </script>
    """
    components.html(button_html, height=48)


def default_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def asset_data_uri(path: str, mime_type: str) -> str:
    asset_path = Path(path)
    if not asset_path.exists():
        return ""
    return f"data:{mime_type};base64,{base64.b64encode(asset_path.read_bytes()).decode('ascii')}"


def render_brand_shell() -> None:
    bg = asset_data_uri("assets/brand-background.jpg", "image/jpeg")
    logo = asset_data_uri("assets/brand-mark.png", "image/png")
    st.markdown(
        f"""
        <style>
            :root {{
                --ink: #f6f6f6;
                --muted: rgba(246, 246, 246, .62);
                --line: rgba(255, 255, 255, .13);
                --line-strong: rgba(255, 255, 255, .24);
                --panel: rgba(13, 13, 13, .76);
                --panel-soft: rgba(22, 22, 22, .58);
                --field: rgba(255,255,255,.085);
            }}

            .stApp {{
                color: var(--ink);
                background:
                    radial-gradient(circle at 86% 78%, rgba(255,255,255,.12), transparent 10%),
                    linear-gradient(90deg, rgba(0,0,0,.92), rgba(0,0,0,.66) 52%, rgba(0,0,0,.90)),
                    url("{bg}");
                background-size: cover;
                background-position: center;
                background-attachment: fixed;
            }}

            [data-testid="stAppViewContainer"] > .main {{
                background: transparent;
            }}

            [data-testid="stHeader"] {{
                background: transparent;
            }}

            [data-testid="stSidebar"] {{
                display: none;
            }}

            [data-testid="collapsedControl"] {{
                display: none;
            }}

            .block-container {{
                max-width: min(1560px, calc(100vw - 56px));
                min-height: 100vh;
                padding: 2rem 0 1.5rem;
            }}

            .brand-hero {{
                display: grid;
                grid-template-columns: 132px 1fr auto;
                align-items: center;
                gap: 14px;
                margin: 0 0 22px;
                padding: 16px;
                border: 1px solid var(--line-strong);
                border-radius: 10px;
                background: linear-gradient(135deg, rgba(20,20,20,.82), rgba(7,7,7,.72));
                box-shadow: 0 22px 70px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.08);
                backdrop-filter: blur(22px);
            }}

            [data-testid="stHorizontalBlock"] {{
                align-items: stretch;
            }}

            [data-testid="column"] {{
                min-width: 0;
            }}

            .brand-logo {{
                width: 118px;
                height: 82px;
                border: 0;
                background: #050505 url("{logo}") center/cover no-repeat;
            }}

            .brand-kicker {{
                display: inline-flex;
                margin: 0 0 8px;
                padding: 4px 8px;
                color: rgba(255,255,255,.78);
                border: 1px solid rgba(255,255,255,.18);
                border-radius: 5px;
                background: rgba(255,255,255,.10);
                font-size: 12px;
                line-height: 1;
                letter-spacing: 0;
                text-transform: uppercase;
            }}

            .brand-title {{
                margin: 0;
                color: var(--ink);
                font-family: Georgia, "Times New Roman", serif;
                font-size: 35px;
                line-height: 1;
                font-weight: 500;
                letter-spacing: 0;
            }}

            .brand-subtitle {{
                margin: 8px 0 0;
                color: var(--muted);
                font-size: 14px;
                line-height: 1.45;
            }}

            .configure-pill {{
                align-self: start;
                padding: 6px 11px;
                color: #fff;
                border: 1px solid rgba(255,255,255,.22);
                border-radius: 6px;
                background: rgba(0,0,0,.42);
                font-size: 12px;
            }}

            .console-footer {{
                margin: 22px 0 0;
                text-align: center;
                color: rgba(255,255,255,.60);
                font-size: 12px;
            }}

            .mini-icons {{
                margin-bottom: 8px;
                letter-spacing: 7px;
                color: rgba(255,255,255,.72);
            }}

            h1, h2, h3, .stMarkdown, label, p, span {{
                color: var(--ink);
            }}

            h2, h3 {{
                letter-spacing: 0;
                font-weight: 560;
            }}

            div[data-testid="stVerticalBlock"] > div:has(> [data-testid="stMarkdownContainer"] .panel-title) {{
                min-height: 100%;
                padding: 14px;
                border: 1px solid var(--line-strong);
                border-radius: 10px;
                background: linear-gradient(180deg, rgba(24,24,24,.76), rgba(9,9,9,.72));
                box-shadow: 0 16px 48px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06);
                backdrop-filter: blur(20px);
            }}

            .panel-title {{
                margin: 0 0 12px;
                color: rgba(255,255,255,.92);
                font-size: 12px;
                font-weight: 650;
                line-height: 1;
                letter-spacing: .02em;
                text-transform: uppercase;
            }}

            .active-meta {{
                margin-top: 11px;
                padding: 12px;
                border-radius: 8px;
                background: rgba(255,255,255,.08);
                color: rgba(255,255,255,.78);
                font-size: 12px;
                line-height: 1.45;
            }}

            div[data-testid="stVerticalBlockBorderWrapper"],
            div[data-testid="stExpander"],
            div[data-testid="stDataFrame"],
            div[data-testid="stChatMessage"] {{
                border-color: var(--line) !important;
                background: var(--panel-soft) !important;
                backdrop-filter: blur(16px);
            }}

            textarea, input, select, [data-baseweb="select"] > div {{
                color: #f7f7f7 !important;
                background: var(--field) !important;
                border-color: rgba(255,255,255,.22) !important;
                border-radius: 7px !important;
            }}

            textarea:focus, input:focus {{
                border-color: rgba(255,255,255,.78) !important;
                box-shadow: 0 0 0 1px rgba(255,255,255,.35) !important;
            }}

            .stButton > button {{
                min-height: 38px;
                border-radius: 7px;
                border: 1px solid rgba(255,255,255,.28);
                color: #fff;
                background: rgba(255,255,255,.06);
                transition: border-color .16s ease, background .16s ease, transform .16s ease;
            }}

            .stButton > button:hover {{
                border-color: rgba(255,255,255,.72);
                background: rgba(255,255,255,.14);
                transform: translateY(-1px);
            }}

            .stButton > button[kind="primary"] {{
                color: #fff;
                background: linear-gradient(180deg, #5fa8f2, #2f74bd);
                border-color: rgba(255,255,255,.34);
                box-shadow: inset 0 1px 0 rgba(255,255,255,.35), 0 14px 30px rgba(39,113,188,.24);
            }}

            .stAlert {{
                border-radius: 8px;
                border: 1px solid var(--line);
                background: rgba(0,0,0,.72);
            }}

            div[data-testid="stTextArea"] textarea {{
                min-height: clamp(92px, 13vh, 170px);
            }}

            div[data-testid="stDataFrame"] {{
                max-height: 34vh;
                overflow: auto;
            }}

            .execution-row {{
                display: flex;
                align-items: flex-start;
                gap: 8px;
                padding: 8px 9px;
                border: 1px solid rgba(255,255,255,.11);
                border-bottom: 0;
                background: rgba(255,255,255,.045);
                color: rgba(255,255,255,.84);
                font-size: 12px;
                line-height: 1.35;
            }}

            .execution-row:last-child {{
                border-bottom: 1px solid rgba(255,255,255,.11);
                border-radius: 0 0 7px 7px;
            }}

            .execution-icon {{
                width: 17px;
                height: 17px;
                display: inline-grid;
                place-items: center;
                flex: 0 0 auto;
                border-radius: 4px;
                background: #fff;
                color: #0a0a0a;
                font-size: 11px;
                font-weight: 700;
            }}

            code, pre {{
                color: #f6f6f6 !important;
                background: rgba(0,0,0,.58) !important;
            }}

            @media (max-width: 760px) {{
                .block-container {{
                    max-width: calc(100vw - 28px);
                    padding-top: 1rem;
                }}
                .brand-hero {{
                    grid-template-columns: 72px 1fr;
                    padding: 14px;
                }}
                .brand-logo {{
                    width: 66px;
                    height: 54px;
                }}
                .configure-pill {{
                    display: none;
                }}
                .brand-title {{
                    font-size: 24px;
                }}
            }}

            @media (min-width: 1180px) {{
                .console-footer {{
                    position: fixed;
                    left: 50%;
                    bottom: 18px;
                    transform: translateX(-50%);
                }}
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="brand-hero">
            <div class="brand-logo" aria-label="Brand mark"></div>
            <div>
                <p class="brand-kicker">ProofHub automation console</p>
                <h1 class="brand-title">The Task Orchestrator</h1>
                <p class="brand-subtitle">Obsessed with creating <em>excellent</em> tasks in any format.</p>
            </div>
            <div class="configure-pill">Configure</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="ProofHub Task Orchestrator",
        page_icon="PH",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    init_state()
    render_brand_shell()

    secret_api_key = default_secret("PROOFHUB_API_KEY")
    api_key = secret_api_key
    base_url = DEFAULT_BASE_URL
    company_url = DEFAULT_COMPANY_URL
    auth_header = "X-API-KEY"
    account_endpoint = DEFAULT_CONNECTION_TEST_PATH
    default_project_id = DEFAULT_PROJECT_ID
    default_tasklist_id = DEFAULT_TASKLIST_ID
    dry_run = True
    raw_status_map = "todo=To Do\nin progress=In Progress\ndone=Completed\nblocked=Blocked"
    raw_bucket_map = (
        f"default={DEFAULT_TASKLIST_ID}\n"
        f"seo={DEFAULT_TASKLIST_ID}\n"
        "ui/ux=\nfrontend=\nbackend=\nqa=\nsecurity=\ndeployment=\nvoice=\noperations="
    )
    raw_label_map = "ui/ux=\nbackend=\nqa=\nsecurity=\nseo=\nvoice=\ndeployment=\nhigh=\nmedium=\nlow="
    known_work_titles = ""
    create_endpoint = "/projects/{project_id}/todolists/{tasklist_id}/tasks"
    create_subtask_endpoint = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}/subtasks"
    update_endpoint = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}"
    list_tasks_endpoint = "/projects/{project_id}/todolists/{tasklist_id}/tasks"
    create_project_endpoint = "/projects"
    create_tasklist_endpoint = "/projects/{project_id}/todolists"
    labels_endpoint = "/labels"
    create_label_endpoint = "/labels"
    create_standalone_projects = True
    create_roadmap_tasklists = True
    auto_create_missing_labels = False
    update_matching_titles = True
    skip_matching_subtasks = True

    with st.expander("Configure", expanded=False):
        cfg_left, cfg_right = st.columns(2, gap="large")
        with cfg_left:
            api_key = st.text_input("ProofHub API key", value=secret_api_key, type="password")
            base_url = st.text_input("API base URL", value=DEFAULT_BASE_URL)
            company_url = st.text_input("Company URL", value=DEFAULT_COMPANY_URL)
            auth_header = st.selectbox("API key header", ["X-API-KEY", "X-Auth-Token", "Authorization"])
            account_endpoint = st.text_input("Account check path", value=DEFAULT_CONNECTION_TEST_PATH)
            dry_run = st.toggle("Dry run", value=True, help="Preview parsing and payloads without calling ProofHub.")
        with cfg_right:
            raw_bucket_map = st.text_area("Bucket map", value=raw_bucket_map, height=156)
            raw_label_map = st.text_area("Label map", value=raw_label_map, height=112, help="Use ProofHub label IDs, for example `ui/ux=12254912`.")
            known_work_titles = st.text_area("Known ongoing work", value="", height=92)
            raw_status_map = st.text_area("Status map", value=raw_status_map, height=92)
            create_endpoint = st.text_input("Create task path", value=create_endpoint)
            create_subtask_endpoint = st.text_input("Create subtask path", value=create_subtask_endpoint)
            update_endpoint = st.text_input("Update task path", value=update_endpoint)
            list_tasks_endpoint = st.text_input("List tasks path", value=list_tasks_endpoint)
            create_project_endpoint = st.text_input("Create project path", value=create_project_endpoint)
            create_tasklist_endpoint = st.text_input("Create tasklist path", value=create_tasklist_endpoint)
            labels_endpoint = st.text_input("Labels path", value=labels_endpoint)
            create_label_endpoint = st.text_input("Create label path", value=create_label_endpoint)
            create_standalone_projects = st.toggle("Create standalone projects", value=True)
            create_roadmap_tasklists = st.toggle("Create roadmap tasklists", value=True)
            auto_create_missing_labels = st.toggle("Auto-create missing labels", value=False)
            update_matching_titles = st.toggle("Update matching task titles", value=True)
            skip_matching_subtasks = st.toggle("Skip matching subtasks", value=True)

    if auth_header == "Authorization" and api_key and not api_key.lower().startswith("bearer "):
        api_key_for_client = f"Bearer {api_key}"
    else:
        api_key_for_client = api_key

    defaults = {"project_id": default_project_id.strip(), "tasklist_id": default_tasklist_id.strip()}
    status_map = parse_status_map(raw_status_map)
    bucket_map = parse_bucket_map(raw_bucket_map, default_tasklist_id.strip())
    label_map = effective_label_map(parse_label_map(raw_label_map))
    known_titles = [line.strip() for line in known_work_titles.splitlines() if line.strip()]

    left, right = st.columns([0.28, 0.72], gap="large")

    with left:
        st.markdown('<p class="panel-title">Project Setup</p>', unsafe_allow_html=True)
        default_project_id = st.text_input("Project ID", value=DEFAULT_PROJECT_ID, label_visibility="visible")
        default_tasklist_id = st.text_input("Tasklist ID", value=DEFAULT_TASKLIST_ID, label_visibility="visible")
        defaults = {"project_id": default_project_id.strip(), "tasklist_id": default_tasklist_id.strip()}
        bucket_map = parse_bucket_map(raw_bucket_map, default_tasklist_id.strip())
        label_map = effective_label_map(parse_label_map(raw_label_map))
        if st.button("API Connection Check", width="stretch"):
            if not api_key_for_client:
                st.session_state.connection_result = {
                    "level": "error",
                    "message": "Enter a ProofHub API key in Configure.",
                }
            else:
                client = ProofHubClient(api_key_for_client, base_url, auth_header, company_url)
                try:
                    response = client.check_connection(account_endpoint)
                    account_name = extract_account_name(response)
                    st.session_state.connection_result = {
                        "level": "success",
                        "message": (
                            f"Connected to {account_name}."
                            if account_name
                            else "Connected to ProofHub."
                        ),
                        "response": response,
                    }
                except ProofHubError as exc:
                    st.session_state.connection_result = {
                        "level": "error",
                        "message": str(exc),
                        "status_code": exc.status_code,
                        "body": exc.body,
                    }
        if st.button("Fetch Labels", width="stretch"):
            if not api_key_for_client:
                st.session_state.label_fetch_result = {
                    "level": "error",
                    "message": "Enter a ProofHub API key in Configure.",
                    "labels": {},
                }
            else:
                client = ProofHubClient(api_key_for_client, base_url, auth_header, company_url)
                try:
                    fetched_labels = label_map_from_records(client.list_labels(labels_endpoint))
                    st.session_state.label_fetch_result = {
                        "level": "success",
                        "message": f"Fetched {len(fetched_labels)} ProofHub labels.",
                        "labels": fetched_labels,
                    }
                    label_map = effective_label_map(parse_label_map(raw_label_map))
                except ProofHubError as exc:
                    st.session_state.label_fetch_result = {
                        "level": "error",
                        "message": str(exc),
                        "labels": {},
                        "status_code": exc.status_code,
                        "body": exc.body,
                    }
        mapped_buckets = {key: value for key, value in bucket_map.items() if key != "default" and value}
        st.caption(f"Routing: {len(mapped_buckets)} mapped buckets | {len(label_map)} label IDs")
        with st.expander("Manage Status Map"):
            st.code(raw_status_map, language="text")
        with st.expander("Manage Routing Maps"):
            st.code("Buckets\n" + raw_bucket_map + "\n\nLabels\n" + raw_label_map, language="text")
        st.markdown(
            f"""
            <div class="active-meta">
                Active Project: {default_project_id}<br>
                Active Tasklist: {default_tasklist_id}
            </div>
            """,
            unsafe_allow_html=True,
        )
        connection_result = st.session_state.connection_result
        if connection_result:
            if connection_result["level"] == "success":
                st.success(connection_result["message"])
            else:
                st.error(connection_result["message"])
        label_fetch_result = st.session_state.label_fetch_result
        if label_fetch_result:
            if label_fetch_result["level"] == "success":
                st.success(label_fetch_result["message"])
                fetched_label_text = label_map_text(label_fetch_result.get("labels", {}))
                if fetched_label_text:
                    st.code(fetched_label_text, language="text")
                    render_copy_button("copy-label-map", "Copy Label Map", "Copied Labels", fetched_label_text)
            else:
                st.error(label_fetch_result["message"])

    with right:
        st.markdown('<p class="panel-title">Task Assistant</p>', unsafe_allow_html=True)
        input_col, button_col = st.columns([0.58, 0.42], gap="medium")
        with input_col:
            raw_text = st.text_area(
                "Daily work notes",
                value=st.session_state.raw_text,
                height=92,
                label_visibility="collapsed",
                placeholder="Describe tasks or updates...",
            )
        with button_col:
            st.write("")
            run_label = "Preview Payloads" if dry_run else "Update ProofHub"
            run_clicked = st.button(run_label, type="primary", width="stretch", disabled=not raw_text.strip())
            render_copy_sample_button(sample_template_text())
        st.session_state.raw_text = raw_text

        parse_result = parse_input(raw_text, defaults)
        routing_decisions = route_tasks(parse_result, status_map, bucket_map, label_map, known_titles)
        validation_errors = validate_execution(parse_result)
        preview_rows = flatten_preview(parse_result.tasks, status_map, label_map)
        mapped_labels, missing_labels = label_coverage(parse_result, label_map)

        if run_clicked:
            if dry_run:
                st.session_state.run_logs.insert(
                    0,
                    {
                        "time": now_local().strftime("%H:%M:%S"),
                        "level": "info",
                        "message": (
                            f'Dry run only: project "{defaults.get("project_id") or "active project"}" would be updated '
                            f"with {len(preview_rows)} prepared ProofHub payloads. No ProofHub API writes were made."
                        ),
                        "response": {
                            "routing_decisions": routing_decisions_json(routing_decisions),
                            "payloads": [row["payload"] for row in preview_rows],
                        },
                    },
                )
            elif validation_errors:
                st.session_state.run_logs.insert(0, error_log("Validation", "Fix validation errors before running.", None, ""))
            elif not api_key_for_client:
                st.session_state.run_logs.insert(0, error_log("Connection", "ProofHub API key is required.", None, ""))
            else:
                executable_parse_result, route_logs = prepare_executable_parse_result(parse_result, routing_decisions, create_standalone_projects)
                client = ProofHubClient(api_key_for_client, base_url, auth_header, company_url)
                synced_label_map, label_logs = sync_label_map(
                    client,
                    label_map,
                    labels_needed_for_parse_result(parse_result),
                    labels_endpoint,
                    create_label_endpoint,
                    auto_create_missing_labels,
                )
                _, unresolved_labels = label_coverage(parse_result, synced_label_map)
                if unresolved_labels:
                    label_logs.append(
                        {
                            "time": now_local().strftime("%H:%M:%S"),
                            "level": "info",
                            "message": (
                                "Tasks will still be created, but these labels need ProofHub IDs before they can be attached: "
                                + ", ".join(unresolved_labels)
                            ),
                        }
                    )
                project_logs = (
                    execute_project_commands(
                        client,
                        parse_result,
                        routing_decisions,
                        create_project_endpoint,
                        create_tasklist_endpoint,
                        create_roadmap_tasklists,
                    )
                    if create_standalone_projects
                    else []
                )
                match_logs = (
                    apply_existing_task_matches(client, executable_parse_result, routing_decisions, list_tasks_endpoint)
                    if update_matching_titles
                    else []
                )
                if executable_parse_result.tasks:
                    logs = execute_tasks(
                        client,
                        executable_parse_result,
                        status_map,
                        synced_label_map,
                        create_endpoint,
                        create_subtask_endpoint,
                        update_endpoint,
                        skip_matching_subtasks,
                    )
                else:
                    logs = []
                st.session_state.run_logs = route_logs + label_logs + project_logs + match_logs + logs + st.session_state.run_logs
            st.rerun()

        st.markdown('<p class="panel-title">Execution Results</p>', unsafe_allow_html=True)
        if parse_result.warnings:
            st.warning("\n".join(parse_result.warnings))
        if validation_errors:
            st.error("\n".join(validation_errors))
        if raw_text.strip():
            mode_text = "Preview only. ProofHub will not be updated until Dry run is off." if dry_run else "Live mode. ProofHub will be updated when you run."
            st.caption(
                f"{mode_text} Labels mapped: {len(mapped_labels)}"
                + (f" | Missing label IDs: {', '.join(missing_labels)}" if missing_labels else " | All detected labels have IDs.")
            )

        if st.session_state.run_logs:
            for item in st.session_state.run_logs[:6]:
                icon = "P" if item.get("level") != "error" else "!"
                st.markdown(
                    f"""
                    <div class="execution-row">
                        <span class="execution-icon">{icon}</span>
                        <span>[{item.get('time', '')}] {item.get('message', '')}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                """
                <div class="execution-row">
                    <span class="execution-icon">P</span>
                    <span>No execution yet. Paste notes and generate an orchestration preview.</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with st.expander("Routing JSON & Payload Preview", expanded=False):
            routing_json = routing_decisions_json(routing_decisions)
            st.json(routing_json)
            if preview_rows:
                st.dataframe(
                    [{key: value for key, value in row.items() if key != "payload"} for row in preview_rows],
                    width="stretch",
                    hide_index=True,
                )
                st.json([row["payload"] for row in preview_rows])

        clear_col, details_col = st.columns([0.3, 0.7])
        with clear_col:
            if st.button("Clear Results", width="stretch"):
                st.session_state.run_logs = []
                st.rerun()
        with details_col:
            st.caption(f"{len(parse_result.tasks)} parent tasks | {sum(len(task.subtasks) for task in parse_result.tasks)} subtasks | dry run {'on' if dry_run else 'off'}")

    st.markdown(
        """
        <div class="console-footer">
            <div class="mini-icons">● ◐ ×</div>
            A minimal solution by Vinay Jain.
        </div>
        """,
        unsafe_allow_html=True,
    )


def merge_raw_text(existing: str, prompt: str) -> str:
    if not existing.strip():
        return prompt.strip()
    return f"{existing.strip()}\n\n{prompt.strip()}"


def assistant_reply(parse_result: ParseResult) -> str:
    if not parse_result.tasks:
        return "I could not find a task yet. Add a title, update ID, due date, status, or subtasks."
    lines = ["Parsed the current task block:"]
    for task in parse_result.tasks:
        lines.append(f"- **{task.title}** ({task_summary(task)})")
        for subtask in task.subtasks:
            lines.append(f"  - {subtask.title} ({task_summary(subtask)})")
    if parse_result.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in parse_result.warnings)
    return "\n".join(lines)


def render_log_item(item: dict[str, Any]) -> None:
    level = item.get("level", "info")
    message = f"`{item.get('time', '')}` {item.get('message', '')}"
    if level == "success":
        st.success(message)
    elif level == "error":
        st.error(message)
    else:
        st.info(message)
    with st.expander("Details", expanded=False):
        st.code(json.dumps({k: v for k, v in item.items() if k != "level"}, indent=2, default=str))


if __name__ == "__main__":
    main()
