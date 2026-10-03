"""Argument parsing and dispatch for ``python -m semantic_layer`` (the ``sl`` CLI).

Every structural step an agent skill takes is one of these commands, so the
skills stay thin and every step is testable. Output is markdown for people and
``--json`` for agents. Modules are imported inside handlers so the pilot path
never loads Phase 2 code.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from semantic_layer.sl.common import Layer, LayerError, emit, load_layer


def _print(data: Any, as_json: bool, markdown: str | None = None) -> None:
    if as_json or markdown is None:
        emit(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        emit(markdown)


# --------------------------------------------------------------------------- handlers


def cmd_mock_seed(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl.mock_warehouse import seed

    paths = seed(layer)
    _print(paths, args.json, "✅ Mock warehouses seeded:\n" + "\n".join(f"- {p}: {path}" for p, path in paths.items()))
    return 0


def cmd_catalog_generate(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import catalog

    snapshot = catalog.collect(layer)
    written = catalog.write(layer, snapshot)
    summary = {p: len(snapshot.tables.get(p, {})) for p in ("legacy", "cloud")}
    _print({"tables": summary, "files": len(written)}, args.json,
           f"✅ Catalog generated: {summary['legacy']} legacy + {summary['cloud']} cloud tables, "
           f"{sum(1 for r in snapshot.migration if r['status'] == 'pending')} not yet migrated")
    return 0


def cmd_validate(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl.validate import validate

    report = validate(layer)
    lines = [f"❌ {e}" for e in report.errors] + [f"⚠️ {w}" for w in report.warnings]
    lines.append("✅ validate: no errors" if report.ok else f"❌ validate: {len(report.errors)} error(s)")
    _print(report.as_dict(), args.json, "\n".join(lines))
    return 0 if report.ok else 1


def cmd_build(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import build

    if args.navigator:
        layer.require_phase2("navigator", args.force)
    if args.check:
        stale = build.check(layer)
        if stale:
            _print({"stale": stale}, args.json, "❌ build/ is stale — run `sl build` and commit:\n" +
                   "\n".join(f"- {s}" for s in stale))
            return 1
        _print({"stale": []}, args.json, "✅ build/ is up to date")
        return 0
    written = build.build(layer)
    if args.navigator:
        from semantic_layer.sl.phase2 import navigator

        written.append(navigator.write(layer))
    _print({"written": [str(p) for p in written]}, args.json, f"✅ build: {len(written)} files written")
    return 0


def cmd_find(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import find

    handler = {"search": find.search, "show": find.show, "owners": find.owners, "table": find.table,
               "lineage": find.lineage, "health": find.health}[args.command]
    data, markdown = handler(layer, args)
    _print(data, args.json, markdown)
    return 0


def cmd_harvest(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import harvest

    if args.harvest_command == "analyse":
        folder = harvest.analyse(layer, Path(args.query), args.platform, args.session, run_probes=not args.no_probes)
        questions = json.loads((folder / "questions.json").read_text(encoding="utf-8"))
        markdown = f"✅ Session {args.session} analysed → {folder}\n\n## Interview questions\n" + "\n".join(
            f"- **{q['id']}** ({q['topic']}) {q['question']}" + (f"  _guess: {q['guess']}_" if q.get("guess") else "")
            for q in questions)
        _print({"session": str(folder), "questions": questions}, args.json, markdown)
        return 0
    if args.harvest_command == "draft":
        written = harvest.draft(layer, args.session, overwrite=args.overwrite)
        _print({"written": [str(p) for p in written]}, args.json,
               "✅ Drafts written (status: draft — owners certify in review):\n" +
               "\n".join(f"- {p.relative_to(layer.root).as_posix()}" for p in written))
        return 0
    if args.harvest_command == "bulk":
        layer.require_phase2("bulk_harvest", args.force)
        from semantic_layer.sl.phase2 import bulk

        report = bulk.run(layer, folder=Path(args.folder) if args.folder else None, history=args.history,
                          dialect=args.dialect)
        _print(report, args.json, bulk.markdown(report))
        return 0
    raise LayerError(f"Unknown harvest command {args.harvest_command}")


def cmd_ask(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import ask

    if args.ask_command == "resolve":
        result = ask.resolve_text(layer, args.text)
        _print(result, args.json, ask.resolve_markdown(result))
        return 0
    spec_text = sys.stdin.read() if args.spec == "-" else Path(args.spec).read_text(encoding="utf-8")
    answer = ask.run(layer, json.loads(spec_text), platform=args.platform, execute=not args.no_execute)
    _print(answer, args.json, ask.answer_markdown(answer))
    return 0 if answer["ok"] else 1


def cmd_capture(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import capture

    if args.capture_command == "definition":
        fields = json.loads(Path(args.fields).read_text(encoding="utf-8")) if args.fields else {}
        path = capture.definition(layer, args.kind, args.id, args.team, args.steward, args.note, fields)
        _print({"written": str(path)}, args.json, capture.definition_markdown(layer, path))
        return 0
    body = capture.issue(layer, args.about, args.note, args.reported_by)
    _print({"issue": body}, args.json, body["markdown"])
    return 0


def cmd_checks(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import checks

    if args.full:
        layer.require_phase2("full_checks", args.force)
    report = checks.run(layer, today=args.today, full=args.full)
    _print(report, args.json, checks.markdown(report))
    if report["summary"]["failing"]:
        return 1
    # 'unknown' is its own outcome — a check that could not run is not a pass.
    return 3 if report["summary"]["unknown"] or report["catalog"]["status"] == "unknown" else 0


def cmd_disagree(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import disagree

    if args.all:
        layer.require_phase2("bulk_harvest", args.force)
    report = disagree.run(layer, all_queries=args.all)
    _print(report, args.json, disagree.markdown(report))
    return 0


def cmd_eval(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import evals

    report = evals.run(layer)
    _print(report, args.json, evals.markdown(report))
    return 0 if report["passed"] else 1


def cmd_export(layer: Layer, args: argparse.Namespace) -> int:
    layer.require_phase2("osi_export", args.force)
    from semantic_layer.sl.phase2 import export_osi

    path = export_osi.write(layer, platform=args.platform)
    _print({"written": str(path)}, args.json, f"✅ OSI export (experimental) written to {path}")
    return 0


def cmd_init(layer: Layer, args: argparse.Namespace) -> int:
    from semantic_layer.sl import init

    if not args.yes:
        raise LayerError("`sl init --clean` deletes every definition, query, session and generated file in "
                         f"{layer.root}. Re-run with --yes to confirm.")
    removed = init.clean(layer)
    _print({"removed": removed}, args.json, f"✅ Layer emptied ({removed} files removed). Next: `sl catalog generate`, "
                                            "then harvest your first query.")
    return 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    # Global options are accepted before *or* after the subcommand (agents put them last).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="Layer root (default: the semantic_layer/ folder)")
    common.add_argument("--config", default=argparse.SUPPRESS, help="Config file (default: <root>/config.json)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Machine-readable output")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog="sl", description="Team semantic layer toolkit", parents=[common])
    parser.set_defaults(root=None, config=None, json=False, verbose=False)
    sub = parser.add_subparsers(dest="command", required=True)

    mock = sub.add_parser("mock", parents=[common], help="Mock warehouses")
    mock.add_argument("mock_command", choices=["seed"])
    mock.set_defaults(func=cmd_mock_seed)

    cat = sub.add_parser("catalog", parents=[common], help="Physical catalog")
    cat.add_argument("catalog_command", choices=["generate"])
    cat.set_defaults(func=cmd_catalog_generate)

    sub.add_parser("validate", parents=[common], help="CI gate over all definitions").set_defaults(func=cmd_validate)

    build = sub.add_parser("build", parents=[common], help="Regenerate build/ (index, model.json, docs)")
    build.add_argument("--check", action="store_true", help="Fail if committed build/ is stale")
    build.add_argument("--navigator", action="store_true", help="[Phase 2] also write build/navigator.html")
    build.add_argument("--force", action="store_true", help="Run a disabled Phase 2 module once")
    build.set_defaults(func=cmd_build)

    search = sub.add_parser("search", parents=[common], help="Search names, synonyms and descriptions")
    search.add_argument("text")
    show = sub.add_parser("show", parents=[common], help="One definition with provenance and usage")
    show.add_argument("id")
    owners = sub.add_parser("owners", parents=[common], help="What each team owns")
    owners.add_argument("--team")
    table = sub.add_parser("table", parents=[common], help="Definitions using a table, and its migration status")
    table.add_argument("name")
    lineage = sub.add_parser("lineage", parents=[common], help="What a definition uses and what uses it")
    lineage.add_argument("id")
    health = sub.add_parser("health", parents=[common], help="Latest check results and review dates")
    health.add_argument("--overdue", action="store_true")
    health.add_argument("--today")
    for p in (search, show, owners, table, lineage, health):
        p.set_defaults(func=cmd_find)

    harvest = sub.add_parser("harvest", parents=[common], help="Learn definitions from working SQL")
    hsub = harvest.add_subparsers(dest="harvest_command", required=True)
    analyse = hsub.add_parser("analyse", parents=[common], help="parse → cross-reference → probe → interview questions")
    analyse.add_argument("query")
    analyse.add_argument("--platform", choices=["legacy", "cloud"], required=True)
    analyse.add_argument("--session", required=True)
    analyse.add_argument("--no-probes", action="store_true")
    draft = hsub.add_parser("draft", parents=[common], help="answers.yaml + parse → draft definitions")
    draft.add_argument("session")
    draft.add_argument("--overwrite", action="store_true")
    bulk = hsub.add_parser("bulk", parents=[common], help="[Phase 2] usage ranking over many queries")
    bulk.add_argument("--folder")
    bulk.add_argument("--history", action="store_true", help="Read the cloud query history table")
    bulk.add_argument("--dialect", help="Dialect for --folder files without a .<dialect>.sql suffix")
    bulk.add_argument("--force", action="store_true")
    harvest.set_defaults(func=cmd_harvest)

    ask = sub.add_parser("ask", parents=[common], help="Question → certified answer pipeline")
    asub = ask.add_subparsers(dest="ask_command", required=True)
    resolve = asub.add_parser("resolve", parents=[common], help="Match business terms; flag ambiguity")
    resolve.add_argument("text")
    run = asub.add_parser("run", parents=[common], help="Run an analysis spec (JSON file or - for stdin)")
    run.add_argument("spec")
    run.add_argument("--platform", choices=["legacy", "cloud"])
    run.add_argument("--no-execute", action="store_true")
    ask.set_defaults(func=cmd_ask)

    capture = sub.add_parser("capture", parents=[common], help="Turn an in-session clarification into a draft or an issue")
    csub = capture.add_subparsers(dest="capture_command", required=True)
    cdef = csub.add_parser("definition", parents=[common])
    cdef.add_argument("--kind", required=True, choices=["entity", "metric", "dimension", "filter"])
    cdef.add_argument("--id", required=True)
    cdef.add_argument("--team", required=True)
    cdef.add_argument("--steward", required=True)
    cdef.add_argument("--note", required=True, help="What the user said, in their words")
    cdef.add_argument("--fields", help="JSON file with name/description/synonyms/bindings…")
    cissue = csub.add_parser("issue", parents=[common])
    cissue.add_argument("--about", required=True, help="Definition id the problem concerns")
    cissue.add_argument("--note", required=True)
    cissue.add_argument("--reported-by", default="agent session")
    capture.set_defaults(func=cmd_capture)

    checks = sub.add_parser("checks", parents=[common], help="Scheduled health checks")
    checks.add_argument("checks_command", choices=["run"])
    checks.add_argument("--full", action="store_true", help="[Phase 2] freshness, quality, reconciliations")
    checks.add_argument("--today", help="Override today's date (YYYY-MM-DD)")
    checks.add_argument("--force", action="store_true")
    checks.set_defaults(func=cmd_checks)

    disagree = sub.add_parser("disagree", parents=[common], help="Same concept, different filters across teams")
    disagree.add_argument("--all", action="store_true", help="[Phase 2] across the whole bulk harvest")
    disagree.add_argument("--force", action="store_true")
    disagree.set_defaults(func=cmd_disagree)

    sub.add_parser("eval", parents=[common], help="Score the golden questions").set_defaults(func=cmd_eval)

    export = sub.add_parser("export", parents=[common], help="[Phase 2] Export to an open standard")
    export.add_argument("format", choices=["osi"])
    export.add_argument("--platform", choices=["legacy", "cloud"], default="cloud")
    export.add_argument("--force", action="store_true")
    export.set_defaults(func=cmd_export)

    init = sub.add_parser("init", parents=[common], help="Start from scratch")
    init.add_argument("--clean", action="store_true", required=True)
    init.add_argument("--yes", action="store_true")
    init.set_defaults(func=cmd_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        layer = load_layer(args.root, args.config)
        return int(args.func(layer, args))
    except LayerError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2
