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


if __name__ == "__main__":
    unittest.main()
