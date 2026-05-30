"""Generate AWS Step Functions ASL (Amazon States Language) from Workflow IR."""

import json
from typing import Any, Dict, List, Optional

from pipeshift.ir.schema import Workflow, WorkflowLink, WorkflowLinkType, WorkflowTask


def generate_step_functions(workflow: Workflow) -> Dict[str, Any]:
    """Convert a Workflow IR to Step Functions ASL definition.

    Returns a dict representing the ASL JSON.
    """
    states: Dict[str, Any] = {}
    first_task: Optional[str] = None

    # Expand worklet instances into their sessions for state generation
    expanded_tasks, expanded_links = _expand_worklets(workflow)

    # Build adjacency from links
    next_map: Dict[str, List[str]] = {}  # task → [next tasks on success]
    fail_map: Dict[str, List[str]] = {}  # task → [next tasks on failure]

    for link in expanded_links:
        if link.link_type == WorkflowLinkType.FAILURE:
            fail_map.setdefault(link.from_task, []).append(link.to_task)
        else:
            next_map.setdefault(link.from_task, []).append(link.to_task)

    # Find the start task (task with no incoming links, or named "Start")
    all_targets = {link.to_task for link in expanded_links}
    for task in expanded_tasks:
        if task.name not in all_targets or task.type == "start":
            if task.type == "start":
                successors = next_map.get(task.name, [])
                if successors:
                    first_task = _state_name(successors[0])
            else:
                if first_task is None:
                    first_task = _state_name(task.name)

    if first_task is None and expanded_tasks:
        first_task = _state_name(expanded_tasks[0].name)

    # Generate states for each non-start task
    for task in expanded_tasks:
        if task.type == "start":
            continue

        state_name = _state_name(task.name)
        state = _build_state(task, next_map, fail_map, expanded_tasks)
        states[state_name] = state

    asl = {
        "Comment": f"Generated from Informatica workflow: {workflow.name}",
        "StartAt": first_task or "End",
        "States": states,
    }

    return asl


def _expand_worklets(workflow: Workflow) -> tuple:
    """Expand worklet instances into their constituent sessions.

    Returns (expanded_tasks, expanded_links) with worklet tasks replaced
    by their internal sessions wired sequentially.
    """
    if not workflow.worklets:
        return workflow.tasks, workflow.links

    expanded_tasks: List[WorkflowTask] = []
    expanded_links: List[WorkflowLink] = list(workflow.links)
    # Map worklet instance name → (first_session, last_session) for link rewiring
    worklet_boundaries: Dict[str, tuple] = {}

    for task in workflow.tasks:
        if task.type == "worklet" and task.name in workflow.worklets:
            wl = workflow.worklets[task.name]
            # Get sessions in order from worklet links
            wl_sessions = [t for t in wl.tasks if t.type == "session"]
            if not wl_sessions:
                expanded_tasks.append(task)
                continue

            # Order sessions by worklet links
            ordered = _order_tasks_by_links(wl_sessions, wl.links)
            worklet_boundaries[task.name] = (ordered[0].name, ordered[-1].name)

            # Add sessions to expanded list
            expanded_tasks.extend(ordered)

            # Add internal sequential links
            for i in range(len(ordered) - 1):
                expanded_links.append(WorkflowLink(
                    from_task=ordered[i].name,
                    to_task=ordered[i + 1].name,
                    link_type=WorkflowLinkType.SUCCESS,
                ))
        else:
            expanded_tasks.append(task)

    # Rewire links that point to/from worklet instances
    rewired_links: List[WorkflowLink] = []
    for link in expanded_links:
        from_task = link.from_task
        to_task = link.to_task
        if from_task in worklet_boundaries:
            from_task = worklet_boundaries[from_task][1]  # last session
        if to_task in worklet_boundaries:
            to_task = worklet_boundaries[to_task][0]  # first session
        rewired_links.append(WorkflowLink(
            from_task=from_task,
            to_task=to_task,
            link_type=link.link_type,
            condition=link.condition,
        ))

    return expanded_tasks, rewired_links


def _order_tasks_by_links(tasks: List[WorkflowTask], links: List[WorkflowLink]) -> List[WorkflowTask]:
    """Order tasks based on workflow links. Falls back to original order."""
    if not links:
        return tasks

    task_map = {t.name: t for t in tasks}
    # Find task with no incoming link among these tasks
    task_names = {t.name for t in tasks}
    has_incoming = {l.to_task for l in links if l.to_task in task_names and l.from_task in task_names}
    starts = [t for t in tasks if t.name not in has_incoming]

    if not starts:
        return tasks

    ordered = []
    next_map = {}
    for l in links:
        if l.from_task in task_names and l.to_task in task_names:
            next_map[l.from_task] = l.to_task

    current = starts[0].name
    visited = set()
    while current and current in task_map and current not in visited:
        visited.add(current)
        ordered.append(task_map[current])
        current = next_map.get(current)

    # Add any remaining tasks not reached by links
    for t in tasks:
        if t.name not in visited:
            ordered.append(t)

    return ordered


def generate_step_functions_json(workflow: Workflow) -> str:
    """Generate Step Functions ASL as a formatted JSON string."""
    return json.dumps(generate_step_functions(workflow), indent=2)


def _build_state(
    task: WorkflowTask,
    next_map: Dict[str, List[str]],
    fail_map: Dict[str, List[str]],
    all_tasks: List[WorkflowTask],
) -> Dict[str, Any]:
    """Build a single ASL state from a workflow task."""
    successors = next_map.get(task.name, [])
    failure_targets = fail_map.get(task.name, [])

    state: Dict[str, Any] = {}

    if task.type == "session":
        # Session → Glue job invocation (or Lambda)
        state["Type"] = "Task"
        state["Resource"] = "arn:aws:states:::glue:startJobRun.sync"
        state["Parameters"] = {
            "JobName.$": f"$.jobs.{_sanitize(task.session_config.mapping_name)}"
            if task.session_config
            else f"$.jobs.{_sanitize(task.name)}",
        }
        # Add session properties as job arguments if present
        if task.session_config and task.session_config.properties:
            args = {}
            for k, v in task.session_config.properties.items():
                if v:
                    args[f"--{_sanitize(k)}"] = v
            if args:
                state["Parameters"]["Arguments"] = args

    elif task.type == "worklet":
        # Worklet → expand as a nested Parallel or sequential sub-states
        state["Type"] = "Pass"
        state["Comment"] = f"Worklet: {task.name} (sessions inlined below)"

    elif task.type == "command":
        state["Type"] = "Task"
        state["Resource"] = "arn:aws:states:::lambda:invoke"
        state["Parameters"] = {
            "FunctionName": f"pipeshift-cmd-{_sanitize(task.name)}",
            "Payload": {"command": task.command or ""},
        }

    elif task.type == "email":
        state["Type"] = "Task"
        state["Resource"] = "arn:aws:states:::sns:publish"
        state["Parameters"] = {
            "TopicArn.$": "$.notification_topic",
            "Message": f"Workflow task notification: {task.name}",
        }

    elif task.type == "decision":
        state["Type"] = "Choice"
        state["Choices"] = []
        state["Default"] = "FailState"
        # Choices would be populated from conditional links
        return state

    else:
        # Unknown task type → Pass state with comment
        state["Type"] = "Pass"
        state["Comment"] = f"Unsupported task type: {task.type}"

    # Wire up error handling (Catch)
    if failure_targets:
        catch_state = _state_name(failure_targets[0])
        state["Catch"] = [
            {
                "ErrorEquals": ["States.ALL"],
                "Next": catch_state,
            }
        ]

    # Wire up next state
    if successors:
        # Filter out Start
        real_successors = [s for s in successors if s.lower() != "start"]
        if real_successors:
            state["Next"] = _state_name(real_successors[0])
        else:
            state["End"] = True
    else:
        state["End"] = True

    return state


def _state_name(task_name: str) -> str:
    """Convert task name to a valid Step Functions state name."""
    return task_name.replace(" ", "_")


def _sanitize(name: str) -> str:
    """Sanitize a name for use in ARNs/parameters."""
    return name.lower().replace(" ", "_").replace("-", "_")
