# ProofHub Task Orchestrator

A lightweight Streamlit app for turning chat or structured text blocks into ProofHub task and subtask create/update calls.

The interface uses a minimal black-and-white visual system with bundled brand assets in `assets/`.

## Run

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

See [DEPLOYMENT.md](DEPLOYMENT.md) for GitHub push steps and Streamlit Community Cloud deployment settings.

## Import From A Text File

Dry-run a structured task file:

```powershell
python proofhub_import.py "C:\path\to\tasks.txt"
```

Call ProofHub after setting the key:

```powershell
$env:PROOFHUB_API_KEY = "your-api-key"
python proofhub_import.py "C:\path\to\tasks.txt" --run
```

Or use the secure prompt runner for the provided task file:

```powershell
.\run_proofhub_import.ps1
```

The importer uses the same parser and ProofHub-compliant payload builder as the Streamlit app.

In the Streamlit app, use **Load provided task text** to load the current pasted task file directly into the raw task box.

## Input format

Use one parent task per block. Add metadata as `Field: value` lines. Put child work under `Subtasks:`.

```text
Task: Onboarding checklist
Description: Prepare customer onboarding workflow
Due: tomorrow 5pm
Status: in progress
Priority: high
Assignees: 12345, 67890
Labels: client, onboarding
Subtasks:
- Draft checklist due tomorrow 2pm
- Review with ops status: blocked

Update #123
Status: done
Notes: shipped to client
```

The parser also accepts fields such as `Project`, `Project ID`, `Tasklist`, `Tasklist ID`, `Task ID`, `Title`, `Description`, `Start`, and `Deadline`.

Leading title markers such as `*`, `#`, `-`, and `_` are removed automatically from parent tasks and subtasks. Keep `Update #123` only when you want to update an existing ProofHub task ID.

## Routing Decisions

Before orchestration, the app emits strict JSON routing decisions:

```json
[
  {
    "action_type": "create_task",
    "target_bucket_id": "271269310285",
    "task_payload": {
      "title": "Task title",
      "description": "Task details",
      "status": "todo",
      "start_date": null,
      "due_date": "2026-07-14"
    },
    "routing_justification": "Routed to bucket `271269310285` because it matched `backend` context keywords."
  }
]
```

Supported `action_type` values:

- `create_task`: safe to create in the selected ProofHub tasklist.
- `update_existing`: daily continuity/update intent; requires a concrete `Update #TASK_ID` before the app updates ProofHub.
- `create_project`: standalone mini-app/tool scope; creates a separate ProofHub project in live mode when **Create standalone projects** is enabled.

Use **Bucket map** to connect semantic names like `ui/ux`, `backend`, `qa`, `security`, and `voice` to real ProofHub tasklist IDs. Blank bucket IDs are ignored, so any unmapped work falls back to the default tasklist.

Use **Label map** to connect semantic labels to real ProofHub label IDs, for example:

```text
ui/ux=12254912
backend=12254913
seo=12254914
qa=12254915
```

When **Auto-create missing labels** is enabled, the app reads `/labels` and creates missing inferred labels before sending task payloads.

To find labels from the app:

1. Open **Configure** and confirm **Labels path** is `/labels`.
2. Enter the ProofHub API key.
3. Click **Fetch Labels** in **Project Setup**.
4. Copy the generated `label-name=label-id` map into **Configure → Label map**.

Keep **Auto-create missing labels** off if your ProofHub account creates labels without returning IDs. The app will still create tasks; it will only attach labels that have known numeric IDs.

Keep **Update matching task titles** on when importing a structured script into a tasklist that may already contain those parent tasks. The app fetches existing tasks from the configured **List tasks path**, matches parent titles, and updates the existing ProofHub task instead of creating a duplicate. Keep **Skip matching subtasks** on to prevent duplicate child tasks when the same script is run again.

## ProofHub settings

The app keeps the ProofHub API key in a password field and never stores it in the log. Because ProofHub deployments and API versions can vary, the sidebar exposes:

- API base URL
- API key header
- company URL
- account check path
- create task endpoint template
- create project endpoint
- create tasklist endpoint
- labels endpoint
- update task endpoint template
- default project and tasklist IDs
- workflow status mappings

Use **Check API connection** after entering the API key. The app calls the configured account check path, confirms the connection, and displays the account, company, workspace, user name, subdomain, or email when ProofHub includes one in the response.

Endpoint templates can use `{project_id}`, `{tasklist_id}`, and `{task_id}` placeholders.

From the current ProofHub URL:

```text
https://smarketers.proofhub.com/bappswift/#app/todos/project-9572720073/list-271269310285
```

Use:

```text
Company URL: https://smarketers.proofhub.com
API base URL: https://smarketers.proofhub.com/api/v3
Account check path: /projects
Project ID: 9572720073
Tasklist ID: 271269310285
```

ProofHub's API v3 docs use company-specific base URLs and `todolists` in task paths:

```text
Create task path: /projects/{project_id}/todolists/{tasklist_id}/tasks
Create subtask path: /projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}/subtasks
Update task path: /projects/{project_id}/todolists/{tasklist_id}/tasks/{task_id}
```

## Safety

Dry run mode is enabled by default. Use it to inspect parsed tasks and generated payloads before calling the ProofHub API.
