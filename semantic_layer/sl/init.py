"""``sl init --clean`` — the "start from scratch" button for a real deployment.

Deletes every mock definition, query, harvest session, catalog snapshot and
generated file under the layer root, keeping the tool (``sl/``), the schemas,
``config.json`` and the docs. What remains validates as an empty layer; the next
steps are ``sl catalog generate`` against the real platforms and harvesting the
first real query.
"""

from __future__ import annotations

import shutil

from semantic_layer.sl.common import Layer, write_yaml

CONTENT_DIRS = ("domains", "catalog", "queries", "build", "harvest/sessions")


def clean(layer: Layer) -> int:
    removed = 0
    for name in CONTENT_DIRS:
        folder = layer.root / name
        if folder.exists():
            removed += sum(1 for p in folder.rglob("*") if p.is_file())
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".gitkeep").write_text("", encoding="utf-8")
    golden = layer.evals / "golden_questions.yaml"
    if golden.exists():
        removed += 1
    write_yaml(golden, {"questions": []})
    return removed
