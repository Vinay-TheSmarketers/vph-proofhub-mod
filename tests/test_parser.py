import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

import app
import proofhub_import


DEFAULTS = {"project_id": "9572720073", "tasklist_id": "271269310285"}


class ParserRegressionTests(unittest.TestCase):
    def test_multiline_structured_subtasks_are_not_overcounted(self) -> None:
        raw_text = """Project: 9572720073
Tasklist: 271269310285

Task: Google Search Console Module Integration
Description: Build the integration layer.
Status: completed
Priority: high
Labels: seo-auto-system, backend, api-integration, data-plumbing
Start Date: 2026-07-01
Due Date: 2026-07-03

Subtask: Document functional capabilities
Status: completed
Due Date: 2026-07-01

Subtask: Build backend validation routines
Status: completed
Due Date: 2026-07-02

---

Task: Production Sandboxing and Live OAuth Validation
Description: Validate OAuth without risking production configurations.
Status: in progress
Priority: high
Labels: security, oauth, devops, testing
Start Date: 2026-07-01
Due Date: 2026-07-12

Subtask: Configure live OAuth application screens
Status: in progress
Due Date: 2026-07-10
"""
        parse_result = app.parse_input(raw_text, DEFAULTS)

        self.assertEqual(len(parse_result.tasks), 2)
        self.assertEqual([len(task.subtasks) for task in parse_result.tasks], [2, 1])
        self.assertEqual(parse_result.tasks[0].subtasks[0].status, "completed")
        self.assertEqual(parse_result.tasks[0].subtasks[0].due_at.date().isoformat(), "2026-07-01")

    def test_production_title_does_not_trigger_project_creation(self) -> None:
        parse_result = app.parse_input(
            """Task: Production Sandboxing and Live OAuth Validation
Description: Stress-test live search pipelines.
Status: in progress
Labels: security, oauth, devops, testing
""",
            DEFAULTS,
        )
        decisions = app.route_tasks(parse_result, {}, {"default": "271269310285"}, {}, [])

        self.assertEqual(decisions[0].action_type, "create_task")

    def test_labels_are_payload_metadata_not_description_or_status_labels(self) -> None:
        parse_result = app.parse_input(
            """Task: Google Search Console Module Integration
Description: Build the integration layer.
Status: completed
Priority: high
Labels: seo-auto-system, backend
""",
            DEFAULTS,
        )
        label_map = app.parse_label_map("seo-auto-system=1\nbackend=2\nhigh=3\ncompleted=4")
        payload = app.build_payload(parse_result.tasks[0], {}, label_map, app.infer_task_labels(parse_result.tasks[0]))

        self.assertNotIn("Labels:", parse_result.tasks[0].description)
        self.assertEqual(payload["labels"], [1, 2, 3])

    def test_explicit_labels_prevent_extra_semantic_label_guessing(self) -> None:
        parse_result = app.parse_input(
            """Task: Production Sandboxing and Live OAuth Validation
Description: Validate OAuth, testing, deployment, and API behavior.
Status: in progress
Priority: high
Labels: security, oauth, devops, testing

Subtask: Conduct end-to-end data processing tests
Status: todo
Due Date: 2026-07-12
""",
            DEFAULTS,
        )
        parent, subtask = parse_result.tasks[0], parse_result.tasks[0].subtasks[0]

        self.assertEqual(app.infer_task_labels(parent), ["high"])
        self.assertEqual(app.infer_task_labels(subtask), [])

    def test_existing_task_title_match_converts_create_to_update(self) -> None:
        parse_result = app.parse_input(
            """Task: Google Search Console Module Integration
Description: Build the integration layer.
Status: completed
""",
            DEFAULTS,
        )
        decisions = [
            app.RoutingDecision(
                action_type="create_task",
                target_bucket_id="271269310285",
                task_payload={},
                routing_justification="test",
            )
        ]

        class FakeClient:
            def list_tasks(self, project_id: str, tasklist_id: str, list_tasks_endpoint: str):
                return [{"id": "54321", "title": "Google Search Console Module Integration"}]

        logs = app.apply_existing_task_matches(FakeClient(), parse_result, decisions, "/unused")

        self.assertEqual(parse_result.tasks[0].action, "update")
        self.assertEqual(parse_result.tasks[0].task_id, "54321")
        self.assertEqual(logs[0]["level"], "info")

    def test_existing_subtask_title_match_is_skipped(self) -> None:
        parse_result = app.parse_input(
            """Task: Google Search Console Module Integration
Status: completed

Subtask: Document functional capabilities
Status: completed
Due Date: 2026-07-01
""",
            DEFAULTS,
        )
        parse_result.tasks[0].action = "update"
        parse_result.tasks[0].task_id = "54321"

        class FakeClient:
            def update_task(self, project_id, tasklist_id, task_id, payload, update_endpoint):
                return {"id": task_id}

            def list_subtasks(self, project_id, tasklist_id, task_id, list_subtasks_endpoint):
                return [{"id": "98765", "title": "Document functional capabilities"}]

            def create_subtask(self, *args, **kwargs):
                raise AssertionError("duplicate subtask should not be created")

        logs = app.execute_tasks(
            FakeClient(),
            parse_result,
            {},
            {},
            "/unused",
            "/unused",
            "/unused",
            "/unused",
            True,
        )

        self.assertTrue(any("skipped duplicate creation" in log["message"] for log in logs))

    def test_created_completed_tasks_are_marked_complete_after_create(self) -> None:
        parse_result = app.parse_input(
            """Task: Google Search Console Module Integration
Status: completed

Subtask: Document functional capabilities
Status: completed
""",
            DEFAULTS,
        )
        calls = []

        class FakeClient:
            def create_task(self, project_id, tasklist_id, payload, create_endpoint):
                calls.append(("create_task", payload))
                return {"id": "54321", "title": payload["title"]}

            def update_task(self, project_id, tasklist_id, task_id, payload, update_endpoint):
                calls.append(("update_task", task_id, payload))
                return {"id": task_id, "title": "Google Search Console Module Integration"}

            def list_subtasks(self, project_id, tasklist_id, task_id, list_subtasks_endpoint):
                return []

            def create_subtask(self, project_id, tasklist_id, task_id, payload, create_subtask_endpoint):
                calls.append(("create_subtask", task_id, payload))
                return {"id": "98765", "title": payload["title"]}

            def update_subtask(self, project_id, tasklist_id, task_id, subtask_id, payload, update_subtask_endpoint):
                calls.append(("update_subtask", task_id, subtask_id, payload))
                return {"id": subtask_id, "title": "Document functional capabilities"}

        logs = app.execute_tasks(
            FakeClient(),
            parse_result,
            {},
            {},
            "/unused",
            "/unused",
            "/unused",
            "/unused",
            True,
        )

        self.assertIn(("update_task", "54321", {"completed": True}), calls)
        self.assertIn(("update_subtask", "54321", "98765", {"completed": True}), calls)
        self.assertTrue(any("marked task" in log["message"] for log in logs))
        self.assertTrue(any("marked subtask" in log["message"] for log in logs))

    def test_tasklist_fetch_result_can_be_copied_as_bucket_map(self) -> None:
        response = {
            "data": {
                "todolists": [
                    {"id": "111", "title": "UI/UX"},
                    {"tasklist_id": "222", "name": "Backend"},
                ]
            }
        }

        tasklist_map = app.tasklist_map_from_records(response)

        self.assertEqual(tasklist_map, {"ui ux": "111", "backend": "222"})
        self.assertEqual(app.bucket_map_text(tasklist_map, "999"), "default=999\nbackend=222\nui ux=111")

    def test_router_matches_semantic_bucket_inside_fetched_tasklist_name(self) -> None:
        parse_result = app.parse_input(
            """Task: Google Search Console Module Integration
Description: Build search console and SEO data ingestion.
Status: in progress
""",
            DEFAULTS,
        )
        bucket_map = app.parse_bucket_map("default=999\nseo auto system=123", "999")

        decisions = app.route_tasks(parse_result, {}, bucket_map, {}, [])

        self.assertEqual(decisions[0].target_bucket_id, "123")

    def test_fetched_label_names_match_slugged_input_labels(self) -> None:
        parse_result = app.parse_input(
            """Task: Google Search Console Module Integration
Labels: seo-auto-system, api-integration
""",
            DEFAULTS,
        )
        label_map = app.label_map_from_records(
            [
                {"id": "11", "name": "SEO Auto System"},
                {"id": "12", "name": "API Integration"},
            ]
        )

        payload = app.build_payload(parse_result.tasks[0], {}, label_map, [])
        mapped, missing = app.label_coverage(parse_result, label_map)

        self.assertEqual(payload["labels"], [11, 12])
        self.assertEqual(mapped, ["seo-auto-system", "api-integration"])
        self.assertEqual(missing, [])

    def test_missing_label_template_is_ready_for_ids(self) -> None:
        self.assertEqual(app.missing_label_template(["backend", "api-integration"]), "backend=\napi-integration=")

    def test_sample_template_uses_bundled_template(self) -> None:
        self.assertEqual(app.sample_template_text(), app.DEFAULT_SAMPLE_TEMPLATE)

    def test_cli_live_run_stops_before_writes_when_labels_are_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "tasks.txt"
            input_file.write_text(
                """Project: 9572720073
Tasklist: 271269310285

Task: Google Search Console Module Integration
Labels: seo-auto-system
""",
                encoding="utf-8",
            )

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass

                def list_labels(self, labels_path):
                    return []

            with (
                patch("sys.argv", ["proofhub_import.py", str(input_file), "--run"]),
                patch.dict("os.environ", {"PROOFHUB_API_KEY": "test-key"}),
                patch("proofhub_import.ProofHubClient", FakeClient),
                patch("sys.stdout", io.StringIO()),
            ):
                exit_code = proofhub_import.main()

        self.assertEqual(exit_code, 4)

    def test_cli_live_run_fetches_labels_automatically(self) -> None:
        with TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "tasks.txt"
            input_file.write_text(
                """Project: 9572720073
Tasklist: 271269310285

Task: Google Search Console Module Integration
Labels: seo-auto-system
""",
                encoding="utf-8",
            )

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass

                def list_labels(self, labels_path):
                    return [{"id": "11", "name": "SEO Auto System"}]

                def list_tasks(self, project_id, tasklist_id, list_tasks_endpoint):
                    return []

                def list_subtasks(self, project_id, tasklist_id, task_id, list_subtasks_endpoint):
                    return []

                def create_task(self, project_id, tasklist_id, payload, create_endpoint):
                    return {"id": "task-1", "title": payload["title"], "project": {"id": project_id}}

            with (
                patch("sys.argv", ["proofhub_import.py", str(input_file), "--run"]),
                patch.dict("os.environ", {"PROOFHUB_API_KEY": "test-key"}),
                patch("proofhub_import.ProofHubClient", FakeClient),
                patch("sys.stdout", io.StringIO()),
            ):
                exit_code = proofhub_import.main()

        self.assertEqual(exit_code, 0)

    def test_single_task_after_global_header_is_not_split_from_header(self) -> None:
        parse_result = app.parse_input(
            """Project: 9572720073
Tasklist: 271269310285

Task: Google Search Console Module Integration
Labels: seo-auto-system
""",
            {"project_id": "", "tasklist_id": ""},
        )

        self.assertEqual(len(parse_result.tasks), 1)
        self.assertEqual(parse_result.tasks[0].title, "Google Search Console Module Integration")
        self.assertEqual(parse_result.tasks[0].project_id, "9572720073")
        self.assertEqual(parse_result.tasks[0].tasklist_id, "271269310285")

    def test_global_project_header_is_detected_inside_intro_sentence(self) -> None:
        parse_result = app.parse_input(
            """make it functional i pasted this in the app Project: 9572720073
Tasklist: 271269310285

Task: Google Search Console Module Integration
Labels: seo-auto-system

---

Task: Production Sandboxing and Live OAuth Validation
Labels: security
""",
            {"project_id": "", "tasklist_id": ""},
        )

        self.assertEqual(len(parse_result.tasks), 2)
        self.assertTrue(all(task.project_id == "9572720073" for task in parse_result.tasks))
        self.assertTrue(all(task.tasklist_id == "271269310285" for task in parse_result.tasks))
        self.assertEqual(parse_result.warnings, [])


if __name__ == "__main__":
    unittest.main()
