"""Analyzer: dependency graph and HTML report generation."""

from typing import Dict, List, Set, Tuple

from pipeshift.ir.schema import Mapping, Repository
from pipeshift.scorer import classify_mapping, score_mapping


def build_dependency_graph(repo: Repository) -> Dict[str, List[str]]:
    """Build a mapping dependency graph based on shared sources/targets.

    If mapping A writes to target X, and mapping B reads from source X,
    then A → B (A must run before B).

    Returns: dict of mapping_name → [list of mappings it feeds into]
    """
    # Collect what each mapping writes to
    writers: Dict[str, str] = {}  # target_name → mapping_name that writes it
    for m in repo.mappings:
        for t in m.targets:
            writers[t.name] = m.name

    # Collect what each mapping reads from
    graph: Dict[str, List[str]] = {m.name: [] for m in repo.mappings}
    for m in repo.mappings:
        for s in m.sources:
            # If this source is another mapping's target, there's a dependency
            if s.name in writers and writers[s.name] != m.name:
                upstream = writers[s.name]
                if m.name not in graph[upstream]:
                    graph[upstream].append(m.name)

    return graph


def generate_html_report(repo: Repository) -> str:
    """Generate an HTML assessment report for the repository."""
    graph = build_dependency_graph(repo)
    total = len(repo.mappings)

    # Score all mappings
    scored = []
    complexity_counts = {"simple": 0, "medium": 0, "complex": 0, "manual": 0}
    for m in repo.mappings:
        score = score_mapping(m)
        complexity = classify_mapping(m).value
        complexity_counts[complexity] += 1
        scored.append((m, score, complexity))

    # Transform type counts
    transform_types: Dict[str, int] = {}
    for m in repo.mappings:
        for t in m.transforms:
            transform_types[t.type.value] = transform_types.get(t.type.value, 0) + 1

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PipeShift Migration Assessment: {repo.name}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px; color: #1a1a1a; }}
h1 {{ color: #0f172a; border-bottom: 2px solid #3b82f6; padding-bottom: 8px; }}
h2 {{ color: #334155; margin-top: 32px; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
th, td {{ border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left; }}
th {{ background: #f1f5f9; font-weight: 600; }}
tr:nth-child(even) {{ background: #f8fafc; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
.simple {{ background: #dcfce7; color: #166534; }}
.medium {{ background: #fef9c3; color: #854d0e; }}
.complex {{ background: #fed7aa; color: #9a3412; }}
.manual {{ background: #fecaca; color: #991b1b; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin: 16px 0; }}
.summary-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; text-align: center; }}
.summary-card .number {{ font-size: 28px; font-weight: 700; color: #1e40af; }}
.summary-card .label {{ font-size: 13px; color: #64748b; margin-top: 4px; }}
.dep-graph {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; font-family: monospace; font-size: 13px; }}
</style>
</head>
<body>
<h1>PipeShift Migration Assessment</h1>
<p><strong>Repository:</strong> {repo.name} &nbsp;|&nbsp; <strong>Folder:</strong> {repo.folder or 'N/A'}</p>

<h2>Summary</h2>
<div class="summary-grid">
<div class="summary-card"><div class="number">{total}</div><div class="label">Mappings</div></div>
<div class="summary-card"><div class="number">{len(repo.workflows)}</div><div class="label">Workflows</div></div>
<div class="summary-card"><div class="number">{len(repo.sources)}</div><div class="label">Sources</div></div>
<div class="summary-card"><div class="number">{len(repo.targets)}</div><div class="label">Targets</div></div>
</div>

<h2>Complexity Distribution</h2>
<table>
<tr><th>Tier</th><th>Count</th><th>Percentage</th></tr>
"""
    for tier in ["simple", "medium", "complex", "manual"]:
        count = complexity_counts[tier]
        pct = (count / total * 100) if total else 0
        html += f'<tr><td><span class="badge {tier}">{tier}</span></td><td>{count}</td><td>{pct:.0f}%</td></tr>\n'

    html += """</table>

<h2>Mappings</h2>
<table>
<tr><th>Mapping</th><th>Score</th><th>Complexity</th><th>Transforms</th><th>Sources</th><th>Targets</th></tr>
"""
    for m, score, complexity in sorted(scored, key=lambda x: -x[1]):
        sources = ", ".join(s.name for s in m.sources) or "—"
        targets = ", ".join(t.name for t in m.targets) or "—"
        html += f'<tr><td>{m.name}</td><td>{score}</td><td><span class="badge {complexity}">{complexity}</span></td>'
        html += f'<td>{len(m.transforms)}</td><td>{sources}</td><td>{targets}</td></tr>\n'

    html += """</table>

<h2>Transform Types</h2>
<table>
<tr><th>Type</th><th>Count</th></tr>
"""
    for t_type, count in sorted(transform_types.items(), key=lambda x: -x[1]):
        html += f'<tr><td>{t_type}</td><td>{count}</td></tr>\n'

    html += """</table>

<h2>Dependency Graph</h2>
<div class="dep-graph">
"""
    if any(deps for deps in graph.values()):
        for mapping_name, dependents in graph.items():
            if dependents:
                for dep in dependents:
                    html += f"{mapping_name} → {dep}<br>\n"
    else:
        html += "<em>No inter-mapping dependencies detected (all mappings are independent).</em>\n"

    html += """</div>

<h2>Workflows</h2>
<table>
<tr><th>Workflow</th><th>Schedule</th><th>Tasks</th></tr>
"""
    for wf in repo.workflows:
        sched = wf.schedule_cron or "unscheduled"
        html += f'<tr><td>{wf.name}</td><td><code>{sched}</code></td><td>{len(wf.tasks)}</td></tr>\n'

    html += """</table>

<footer style="margin-top: 40px; padding-top: 16px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 12px;">
Generated by PipeShift | Informatica PowerCenter → dbt Migration Agent
</footer>
</body>
</html>"""

    return html
