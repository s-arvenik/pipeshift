"""PipeShift CLI: analyze and convert Informatica exports."""

import argparse
import json
import sys
from pathlib import Path

from pipeshift.analyzer import build_dependency_graph, generate_html_report
from pipeshift.generator import generate_dbt_project
from pipeshift.orchestration import generate_step_functions_json
from pipeshift.parser.informatica_xml import parse_file
from pipeshift.scorer import classify_mapping, score_mapping, score_repository


def main():
    parser = argparse.ArgumentParser(
        prog="pipeshift",
        description="Convert Informatica PowerCenter exports to dbt projects",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # analyze command
    analyze_p = subparsers.add_parser("analyze", help="Analyze an Informatica XML export")
    analyze_p.add_argument("input", help="Path to Informatica XML export file")
    analyze_p.add_argument("--json", action="store_true", help="Output as JSON")
    analyze_p.add_argument("--html", metavar="FILE", help="Write HTML report to file")

    # convert command
    convert_p = subparsers.add_parser("convert", help="Convert an Informatica XML export to dbt")
    convert_p.add_argument("input", help="Path to Informatica XML export file or directory")
    convert_p.add_argument("-o", "--output", default="./dbt_output", help="Output directory")

    args = parser.parse_args()

    if args.command == "analyze":
        _cmd_analyze(args)
    elif args.command == "convert":
        _cmd_convert(args)


def _cmd_analyze(args):
    path = Path(args.input)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    repo = parse_file(path)

    # HTML report mode
    if args.html:
        html = generate_html_report(repo)
        Path(args.html).write_text(html)
        print(f"HTML report written to {args.html}")
        return

    # Compute summary stats
    total_mappings = len(repo.mappings)
    total_transforms = sum(len(m.transforms) for m in repo.mappings)
    total_workflows = len(repo.workflows)
    total_sources = len(repo.sources)
    total_targets = len(repo.targets)

    # Complexity breakdown
    transform_types = {}
    for m in repo.mappings:
        for t in m.transforms:
            transform_types[t.type.value] = transform_types.get(t.type.value, 0) + 1

    # Scoring
    scoring = score_repository(repo.mappings)

    report = {
        "repository": repo.name,
        "folder": repo.folder,
        "summary": {
            "mappings": total_mappings,
            "transforms": total_transforms,
            "workflows": total_workflows,
            "sources": total_sources,
            "targets": total_targets,
        },
        "complexity": scoring["summary"],
        "transform_types": transform_types,
        "mappings": [
            {
                "name": m.name,
                "description": m.description,
                "transforms": len(m.transforms),
                "score": score_mapping(m),
                "complexity": classify_mapping(m).value,
                "sources": [s.name for s in m.sources],
                "targets": [t.name for t in m.targets],
            }
            for m in repo.mappings
        ],
        "workflows": [
            {
                "name": w.name,
                "schedule": w.schedule_cron,
                "tasks": len(w.tasks),
            }
            for w in repo.workflows
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Repository: {repo.name}")
        print(f"Folder:     {repo.folder or 'N/A'}")
        print()
        print("── Summary ──")
        print(f"  Mappings:    {total_mappings}")
        print(f"  Transforms:  {total_transforms}")
        print(f"  Workflows:   {total_workflows}")
        print(f"  Sources:     {total_sources}")
        print(f"  Targets:     {total_targets}")
        print()
        print("── Complexity ──")
        for tier, count in scoring["summary"].items():
            pct = (count / total_mappings * 100) if total_mappings else 0
            print(f"  {tier:<10} {count:>4}  ({pct:.0f}%)")
        print()
        print("── Transform Types ──")
        for t_type, count in sorted(transform_types.items(), key=lambda x: -x[1]):
            print(f"  {t_type:<25} {count}")
        print()
        print("── Mappings ──")
        for m in repo.mappings:
            score = score_mapping(m)
            complexity = classify_mapping(m).value
            print(f"  {m.name} (score={score}, {complexity})")
        print()
        print("── Workflows ──")
        for w in repo.workflows:
            sched = w.schedule_cron or "unscheduled"
            print(f"  {w.name} [{sched}]")


def _cmd_convert(args):
    path = Path(args.input)
    if not path.exists():
        print(f"Error: path not found: {path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)

    # Batch mode: directory of XML files
    if path.is_dir():
        xml_files = sorted(path.glob("*.xml"))
        if not xml_files:
            print(f"Error: no XML files found in {path}", file=sys.stderr)
            sys.exit(1)
        print(f"Batch mode: {len(xml_files)} XML files in {path}")
    else:
        xml_files = [path]

    from pipeshift.ir.schema import Repository
    # Parse all files and merge into a single repository
    all_repos = []
    for xml_file in xml_files:
        try:
            repo = parse_file(xml_file)
            all_repos.append(repo)
        except (ValueError, FileNotFoundError) as e:
            print(f"  ⚠ Skipping {xml_file.name}: {e}", file=sys.stderr)

    if not all_repos:
        print("Error: no valid XML files parsed", file=sys.stderr)
        sys.exit(1)

    # Merge repositories into one
    if len(all_repos) == 1:
        merged = all_repos[0]
    else:
        merged = Repository(
            name=all_repos[0].name,
            folder=all_repos[0].folder,
            connections=[c for r in all_repos for c in r.connections],
            sources=[s for r in all_repos for s in r.sources],
            targets=[t for r in all_repos for t in r.targets],
            mappings=[m for r in all_repos for m in r.mappings],
            workflows=[w for r in all_repos for w in r.workflows],
        )

    generated = generate_dbt_project(merged, output_dir)

    # Generate Step Functions for workflows
    for wf in merged.workflows:
        sf_path = output_dir / "orchestration" / f"{wf.name}.asl.json"
        sf_path.parent.mkdir(parents=True, exist_ok=True)
        sf_path.write_text(generate_step_functions_json(wf))
        generated.append(sf_path)

    print(f"Generated {len(generated)} files in {output_dir}/")
    for f in generated:
        print(f"  {f.relative_to(output_dir)}")


if __name__ == "__main__":
    main()
