from __future__ import annotations

import io
import json
import re
from pathlib import Path

from evals import run as evals_run
from tests.fakes.fake_llm import FakeLLM

METRIC_LINE = re.compile(r"^[a-z][a-z0-9_]*=[A-Za-z0-9_.-]{1,40}$")


def test_synthetic_healthcheck_scores_perfectly_with_a_correct_model(settings_factory):
    out = io.StringIO()
    result = evals_run.run(
        "healthcheck",
        evals_run.SYNTHETIC,
        FakeLLM(),
        settings_factory(),
        aggregate_only=False,
        out=out,
    )
    assert result["examples"] == 5 and result["accuracy"] == 1.0
    assert "case hc-01: pass" in out.getvalue()


def test_aggregate_only_prints_metric_lines_and_nothing_else(
    tmp_path: Path, settings_factory, sentinels
):
    # A "private" set whose inputs carry sentinel text; half the answers are wrong.
    cases = tmp_path / "healthcheck"
    cases.mkdir()
    secret = sentinels["names"][0].replace(" ", "")
    rows = [
        {"id": f"p{i}", "input": {"nonce": f"{secret}{i}"}, "expected": {"ok": True}}
        for i in range(4)
    ]
    (cases / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def half_wrong(messages):
        nonce = messages[-1]["content"]
        echo = nonce if nonce.endswith(("0", "2")) else sentinels["transcript_sentences"][0]
        return json.dumps({"ok": True, "echo": echo})

    out = io.StringIO()
    result = evals_run.run(
        "healthcheck",
        tmp_path,
        FakeLLM(half_wrong),
        settings_factory(),
        aggregate_only=True,
        out=out,
    )
    text = out.getvalue()
    assert result["accuracy"] == 0.5
    for line in text.splitlines():
        assert METRIC_LINE.match(line), line
    assert secret not in text and sentinels["transcript_sentences"][0] not in text
