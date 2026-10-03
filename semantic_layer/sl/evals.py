"""``sl eval`` — score the golden questions; a drop blocks the merge.

Each question carries the ``terms`` an agent would extract (the language step
is the skill's job and is not scored here). From the terms on, everything is
deterministic and scored:

- clarification — did resolution ask exactly when it should?
- resolution    — did the terms resolve to the reference spec's entity and dimensions?
- template      — did template-first pick the expected certified template (or none)?
- execution     — did the pipeline produce a lint-clean, executable answer
                  (or refuse with the expected error)?
- reconcile     — did the total reconcile with the anchor when it should?
"""

from __future__ import annotations

from typing import Any

from semantic_layer.sl import ask
from semantic_layer.sl.common import Layer, read_yaml
from semantic_layer.sl.model import load_model, strip_ref

METRICS = ("clarification_accuracy", "resolution_accuracy", "template_accuracy", "execution_success",
           "reconcile_accuracy")


def run(layer: Layer) -> dict[str, Any]:
    path = layer.evals / "golden_questions.yaml"
    questions = (read_yaml(path) or {}).get("questions", []) if path.exists() else []
    model = load_model(layer)
    tallies: dict[str, list[bool]] = {m: [] for m in METRICS}
    results = []
    for item in questions:
        outcome: dict[str, Any] = {"question": item["question"], "checks": {}}
        resolved = ask.resolve_text(layer, item.get("terms") or [item["question"]], model=model)
        expected_clarify = bool(item.get("expect_clarification", False))
        outcome["checks"]["clarification"] = resolved["needs_clarification"] == expected_clarify
        tallies["clarification_accuracy"].append(outcome["checks"]["clarification"])
        spec = item.get("spec")
        if spec and not expected_clarify:
            required = {strip_ref(d) for d in spec.get("dimensions", []) or []} | \
                {strip_ref(f["dimension"]) for f in spec.get("filters", []) or []}
            got = {strip_ref(d) for d in resolved["dimensions"]}
            ok = resolved["entity"] == spec["entity"] and required <= got
            outcome["checks"]["resolution"] = ok
            tallies["resolution_accuracy"].append(ok)
        if spec and "expect_template" in item:
            template, _ = ask.match_template(model, spec)
            ok = (template.id if template else None) == item["expect_template"]
            outcome["checks"]["template"] = ok
            tallies["template_accuracy"].append(ok)
        if spec:
            answer = ask.run(layer, spec, model=model)
            if item.get("expect_error"):
                ok = not answer["ok"] and any(item["expect_error"] in e for e in answer["errors"])
            else:
                ok = answer["ok"]
            outcome["checks"]["execution"] = ok
            outcome["errors"] = answer["errors"]
            tallies["execution_success"].append(ok)
            if "expect_reconcile" in item:
                ok = (answer.get("reconciliation", {}).get("status") == "reconciles") == bool(item["expect_reconcile"])
                outcome["checks"]["reconcile"] = ok
                tallies["reconcile_accuracy"].append(ok)
        outcome["passed"] = all(outcome["checks"].values())
        results.append(outcome)

    thresholds = layer.config.get("evals", {}).get("thresholds", {})
    scores = {m: (round(sum(v) / len(v), 3) if v else None) for m, v in tallies.items()}
    failing = [m for m, score in scores.items()
               if score is not None and m in thresholds and score < float(thresholds[m])]
    return {"questions": len(questions), "scores": scores, "thresholds": thresholds, "failing_metrics": failing,
            "passed": not failing and bool(questions), "results": results}



def markdown(report: dict[str, Any]) -> str:
    lines = [f"## Golden-question evals — {report['questions']} questions", "| metric | score | threshold |",
             "|---|---|---|"]
    for metric, score in report["scores"].items():
        threshold = report["thresholds"].get(metric, "—")
        icon = "❌" if metric in report["failing_metrics"] else ("✅" if score is not None else "—")
        lines.append(f"| {metric} | {icon} {score if score is not None else 'n/a'} | {threshold} |")
    for result in report["results"]:
        if not result["passed"]:
            failed = [k for k, v in result["checks"].items() if not v]
            lines.append(f"- ❌ {result['question']} — failed: {', '.join(failed)}"
                         + (f" ({'; '.join(result.get('errors', []))})" if result.get("errors") else ""))
    lines.append("✅ evals pass" if report["passed"] else "❌ evals below threshold")
    return "\n".join(lines)
