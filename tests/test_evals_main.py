"""The eval runner end to end, offline: it must write its report, with escalations counted."""

import json
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

import run_evals  # noqa: E402
from support_agent.agent import triage  # noqa: E402
from scripted import ScriptedProvider  # noqa: E402
from test_agent import proposal  # noqa: E402


def test_report_is_written(monkeypatch, tmp_path):
    async def fake(ticket, erp, provider=None, **kwargs):
        p = ScriptedProvider([[("get_order", {"order_id": "SO-5001"})], [("submit_proposal", proposal())]])
        return await triage(ticket, p, erp)
    monkeypatch.setattr(run_evals, "run_triage", fake)
    out = tmp_path / "report.json"
    code = anyio.run(run_evals.main, "auto", str(out))
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and report["total"] == 9 and report["escalated"] == 0  # one fixed answer can't pass all
