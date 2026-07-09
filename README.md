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

## ProofHub settings

The app keeps the ProofHub API key in a password field and never stores it in the log. Because ProofHub deployments and API versions can vary, the sidebar exposes:

- API base URL
- API key header
- company URL
- account check path
- create task endpoint template
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
