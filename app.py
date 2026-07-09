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


DEFAULT_COMPANY_URL = "https://smarketers.proofhub.com"
DEFAULT_BASE_URL = f"{DEFAULT_COMPANY_URL}/api/v3"
DEFAULT_TIMEOUT = 30
DEFAULT_CONNECTION_TEST_PATH = "/projects"
DEFAULT_PROJECT_ID = "9572720073"
DEFAULT_TASKLIST_ID = "271269310285"
DEFAULT_PROVIDED_TASK_FILE = (
    r"C:\Users\OrCon\.codex\attachments\cf87045d-fad8-4611-a28f-549e1447733d\pasted-text-2.txt"
)


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

    def check_connection(self, account_endpoint: str) -> dict[str, Any]:
        return self._request("GET", account_endpoint)

    def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
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
        if not line:
            continue
        starts_task = bool(re.match(r"^(task\s*:|update\s+#?\d+)", line, flags=re.I))
        if starts_task and current:
            blocks.append("\n".join(current).strip())
            current = [line]
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
        ("Labels", "labels"),
        ("Assignees", "assignees"),
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
        project_id=defaults.get("project_id"),
        tasklist_id=defaults.get("tasklist_id"),
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


def build_payload(task: ParsedTask, status_map: dict[str, str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": task.title,
    }
    if task.description:
        payload["description"] = task.description
    if task.due_at:
        payload["due_date"] = task.due_at.date().isoformat()
    if task.start_at:
        payload["start_date"] = task.start_at.date().isoformat()
    numeric_labels = numeric_ids(task.labels)
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


def normalize_words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def task_context(task: ParsedTask) -> str:
    parts = [task.title, task.description, task.status or "", " ".join(task.labels)]
    parts.extend(subtask.title for subtask in task.subtasks)
    return " ".join(parts).lower()


def is_standalone_scope(task: ParsedTask) -> bool:
    context = task_context(task)
    standalone_terms = (
        "mini-app",
        "mini app",
        "new app",
        "standalone",
        "separate tool",
        "new tool",
        "web scraper",
        "scraper",
        "automation tool",
        "extension",
        "chrome extension",
        "portal",
        "dashboard",
        "platform",
        "product",
    )
    return any(term in context for term in standalone_terms)


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
    semantic_routes = [
        (("frontend", "front-end", "ui", "ux", "interface", "layout", "design system", "styling"), ("ui/ux", "frontend")),
        (("backend", "api", "database", "postgres", "redis", "server", "worker", "pipeline"), ("backend",)),
        (("qa", "test", "testing", "validation", "bug", "approval"), ("qa", "testing")),
        (("security", "auth", "permission", "cryptographic", "isolation"), ("security",)),
        (("content", "eeat", "seo", "keyword", "metadata", "search console"), ("seo", "content")),
        (("voice", "call", "outbound", "webrtc", "demo"), ("voice", "operations")),
        (("deployment", "docker", "release", "shipping", "infrastructure"), ("deployment", "infrastructure")),
    ]

    for keywords, bucket_names in semantic_routes:
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
    known_titles: list[str],
) -> list[RoutingDecision]:
    decisions: list[RoutingDecision] = []
    for task in parse_result.tasks:
        payload = build_payload(task, status_map)
        payload.setdefault("title", task.title)
        payload.setdefault("description", task.description)
        payload["status"] = task.status or ("done" if payload.get("completed") else "todo")
        payload.setdefault("start_date", task.start_at.date().isoformat() if task.start_at else None)
        payload.setdefault("due_date", task.due_at.date().isoformat() if task.due_at else None)

        if is_standalone_scope(task):
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


def flatten_preview(tasks: list[ParsedTask], status_map: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        rows.append(task_to_row(task, status_map, "parent"))
        for subtask in task.subtasks:
            rows.append(
                task_to_row(
                    subtask,
                    status_map,
                    "subtask",
                    inherited_project_id=task.project_id,
                    inherited_tasklist_id=task.tasklist_id,
                )
            )
    return rows


def task_to_row(
    task: ParsedTask,
    status_map: dict[str, str],
    level: str,
    inherited_project_id: str | None = None,
    inherited_tasklist_id: str | None = None,
) -> dict[str, Any]:
    payload = build_payload(task, status_map)
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
    create_endpoint: str,
    create_subtask_endpoint: str,
    update_endpoint: str,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for task in parse_result.tasks:
        try:
            payload = build_payload(task, status_map)
            if task.action == "create":
                assert task.project_id and task.tasklist_id
                response = client.create_task(task.project_id, task.tasklist_id, payload, create_endpoint)
                created_id = extract_task_id(response)
                logs.append(success_log("created_task", task.title, response, task.project_id, task.tasklist_id))
                for subtask in task.subtasks:
                    subtask.project_id = subtask.project_id or task.project_id
                    subtask.tasklist_id = subtask.tasklist_id or task.tasklist_id
                    subtask.parent_id = subtask.parent_id or created_id
                    sub_payload = build_payload(subtask, status_map)
                    if not subtask.parent_id:
                        logs.append(error_log(subtask.title, "Parent task ID was not present in the create response.", None, ""))
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
        except ProofHubError as exc:
            logs.append(error_log(task.title, str(exc), exc.status_code, exc.body))
        except Exception as exc:
            logs.append(error_log(task.title, f"Unexpected execution error: {exc}", None, ""))
    return logs


def prepare_executable_parse_result(
    parse_result: ParseResult,
    routing_decisions: list[RoutingDecision],
) -> tuple[ParseResult, list[dict[str, Any]]]:
    executable_tasks: list[ParsedTask] = []
    route_logs: list[dict[str, Any]] = []
    for task, decision in zip(parse_result.tasks, routing_decisions):
        if decision.action_type == "create_project":
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


def load_text_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


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
    known_work_titles = ""
    create_endpoint = "/projects/{project_id}/todolists/{tasklist_id}/tasks"
    create_subtask_endpoint = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}/subtasks"
    update_endpoint = "/projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}"

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
            known_work_titles = st.text_area("Known ongoing work", value="", height=92)
            raw_status_map = st.text_area("Status map", value=raw_status_map, height=92)
            create_endpoint = st.text_input("Create task path", value=create_endpoint)
            create_subtask_endpoint = st.text_input("Create subtask path", value=create_subtask_endpoint)
            update_endpoint = st.text_input("Update task path", value=update_endpoint)

    if auth_header == "Authorization" and api_key and not api_key.lower().startswith("bearer "):
        api_key_for_client = f"Bearer {api_key}"
    else:
        api_key_for_client = api_key

    defaults = {"project_id": default_project_id.strip(), "tasklist_id": default_tasklist_id.strip()}
    status_map = parse_status_map(raw_status_map)
    bucket_map = parse_bucket_map(raw_bucket_map, default_tasklist_id.strip())
    known_titles = [line.strip() for line in known_work_titles.splitlines() if line.strip()]

    left, right = st.columns([0.28, 0.72], gap="large")

    with left:
        st.markdown('<p class="panel-title">Project Setup</p>', unsafe_allow_html=True)
        default_project_id = st.text_input("Project ID", value=DEFAULT_PROJECT_ID, label_visibility="visible")
        default_tasklist_id = st.text_input("Tasklist ID", value=DEFAULT_TASKLIST_ID, label_visibility="visible")
        defaults = {"project_id": default_project_id.strip(), "tasklist_id": default_tasklist_id.strip()}
        bucket_map = parse_bucket_map(raw_bucket_map, default_tasklist_id.strip())
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
        st.caption("Status map")
        with st.expander("Manage Status Map"):
            st.code(raw_status_map, language="text")
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
            run_clicked = st.button("Generate & Orchestrate", type="primary", width="stretch", disabled=not raw_text.strip())
            if st.button("Load Sample Text", width="stretch"):
                try:
                    st.session_state.raw_text = load_text_file(DEFAULT_PROVIDED_TASK_FILE)
                    st.rerun()
                except OSError as exc:
                    st.session_state.run_logs.insert(0, error_log("Sample text", str(exc), None, ""))
        st.session_state.raw_text = raw_text

        parse_result = parse_input(raw_text, defaults)
        routing_decisions = route_tasks(parse_result, status_map, bucket_map, known_titles)
        validation_errors = validate_execution(parse_result)
        preview_rows = flatten_preview(parse_result.tasks, status_map)

        if run_clicked:
            if dry_run:
                st.session_state.run_logs.insert(
                    0,
                    {
                        "time": now_local().strftime("%H:%M:%S"),
                        "level": "info",
                        "message": (
                            f'Preview ready: project "{defaults.get("project_id") or "active project"}" would be updated '
                            f"with {len(preview_rows)} prepared ProofHub payloads."
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
                executable_parse_result, route_logs = prepare_executable_parse_result(parse_result, routing_decisions)
                if executable_parse_result.tasks:
                    client = ProofHubClient(api_key_for_client, base_url, auth_header, company_url)
                    logs = execute_tasks(
                        client,
                        executable_parse_result,
                        status_map,
                        create_endpoint,
                        create_subtask_endpoint,
                        update_endpoint,
                    )
                else:
                    logs = []
                st.session_state.run_logs = route_logs + logs + st.session_state.run_logs
            st.rerun()

        st.markdown('<p class="panel-title">Execution Results</p>', unsafe_allow_html=True)
        if parse_result.warnings:
            st.warning("\n".join(parse_result.warnings))
        if validation_errors:
            st.error("\n".join(validation_errors))

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
