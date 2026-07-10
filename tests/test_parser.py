import unittest

import app


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
            True,
        )

        self.assertTrue(any("skipped duplicate creation" in log["message"] for log in logs))


if __name__ == "__main__":
    unittest.main()
