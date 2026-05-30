"""Tests for Workflow → Step Functions ASL generation."""

from pathlib import Path

from pipeshift.orchestration import generate_step_functions, generate_step_functions_json
from pipeshift.parser.informatica_xml import parse_file

SAMPLE_DIR = Path(__file__).parent / "sample_exports"


class TestStepFunctionsFromCustomerDim:
    """Test Step Functions generation from the customer_dim workflow."""

    def setup_method(self):
        repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        self.workflow = repo.workflows[0]
        self.asl = generate_step_functions(self.workflow)

    def test_has_comment(self):
        assert "Comment" in self.asl
        assert "wf_customer_dim_daily" in self.asl["Comment"]

    def test_has_start_at(self):
        assert "StartAt" in self.asl
        assert self.asl["StartAt"] == "s_m_customer_dim"

    def test_has_states(self):
        assert "States" in self.asl
        assert len(self.asl["States"]) >= 1

    def test_session_becomes_glue_task(self):
        states = self.asl["States"]
        session_state = states.get("s_m_customer_dim")
        assert session_state is not None
        assert session_state["Type"] == "Task"
        assert "glue:startJobRun" in session_state["Resource"]

    def test_session_has_job_name(self):
        state = self.asl["States"]["s_m_customer_dim"]
        assert "Parameters" in state
        assert "JobName.$" in state["Parameters"]

    def test_last_task_has_end(self):
        state = self.asl["States"]["s_m_customer_dim"]
        assert state.get("End") is True

    def test_json_output(self):
        json_str = generate_step_functions_json(self.workflow)
        assert '"StartAt"' in json_str
        assert '"States"' in json_str
        assert "glue:startJobRun" in json_str


class TestStepFunctionsMultiTask:
    """Test with a workflow that has multiple tasks and failure handling."""

    def setup_method(self):
        from pipeshift.ir.schema import (
            Workflow, WorkflowTask, WorkflowLink, WorkflowLinkType, SessionConfig,
        )
        self.workflow = Workflow(
            id="wf_multi",
            name="wf_multi_step",
            tasks=[
                WorkflowTask(id="Start", name="Start", type="start"),
                WorkflowTask(
                    id="s_load_staging",
                    name="s_load_staging",
                    type="session",
                    session_config=SessionConfig(mapping_name="m_staging_load"),
                ),
                WorkflowTask(
                    id="s_transform",
                    name="s_transform",
                    type="session",
                    session_config=SessionConfig(mapping_name="m_transform"),
                ),
                WorkflowTask(
                    id="email_failure",
                    name="email_failure",
                    type="email",
                ),
            ],
            links=[
                WorkflowLink(from_task="Start", to_task="s_load_staging", link_type=WorkflowLinkType.SUCCESS),
                WorkflowLink(from_task="s_load_staging", to_task="s_transform", link_type=WorkflowLinkType.SUCCESS),
                WorkflowLink(from_task="s_load_staging", to_task="email_failure", link_type=WorkflowLinkType.FAILURE),
                WorkflowLink(from_task="s_transform", to_task="email_failure", link_type=WorkflowLinkType.FAILURE),
            ],
        )
        self.asl = generate_step_functions(self.workflow)

    def test_starts_at_first_session(self):
        assert self.asl["StartAt"] == "s_load_staging"

    def test_has_three_states(self):
        # Start is skipped, so: s_load_staging, s_transform, email_failure
        assert len(self.asl["States"]) == 3

    def test_first_task_chains_to_second(self):
        state = self.asl["States"]["s_load_staging"]
        assert state["Next"] == "s_transform"

    def test_last_session_ends(self):
        state = self.asl["States"]["s_transform"]
        assert state.get("End") is True

    def test_failure_link_becomes_catch(self):
        state = self.asl["States"]["s_load_staging"]
        assert "Catch" in state
        assert state["Catch"][0]["ErrorEquals"] == ["States.ALL"]
        assert state["Catch"][0]["Next"] == "email_failure"

    def test_email_task_uses_sns(self):
        state = self.asl["States"]["email_failure"]
        assert state["Type"] == "Task"
        assert "sns:publish" in state["Resource"]

    def test_both_sessions_catch_to_email(self):
        s1 = self.asl["States"]["s_load_staging"]
        s2 = self.asl["States"]["s_transform"]
        assert s1["Catch"][0]["Next"] == "email_failure"
        assert s2["Catch"][0]["Next"] == "email_failure"
