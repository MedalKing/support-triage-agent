"""The admin Evals page and the results index read the saved reports; nothing else may be served."""

import json

from fastapi.testclient import TestClient

import support_agent.api as api
from support_agent import eval_results


def report(provider, passed, cost=None, cache=None):
    data = {"provider": provider, "passed": passed, "total": 9, "escalated": 0,
            "cases": [{"ticket": "T-1001", "passed": True}]}
    if cost is not None:
        data["totals"] = {"cost_usd": cost, "median_seconds": 8.0, "cached_share": 0.5}
        data["prompt_cache"] = cache
    return json.dumps(data)


def folder(tmp_path):
    (tmp_path / "2026-11-26-1236-claude_claude-haiku-5-5.json").write_text(report("claude:claude-haiku-5-5", 7))
    (tmp_path / "2026-11-26-caching").mkdir()
    (tmp_path / "2026-11-26-caching" / "1331-auto.json").write_text(
        report("auto:claude-haiku-5-5->claude-opus-5-5", 9, cost=0.018, cache=True))
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "2026-11-20-0900-old.json").write_text(report("claude:claude-opus-5-5", 3))
    (tmp_path / "2026-11-26-sample.json").write_text("[]")  # a sample, not a report
    return tmp_path


def test_current_reports_newest_first_without_archive_or_samples(tmp_path):
    rows = eval_results.runs(folder(tmp_path))
    assert [r["id"] for r in rows] == ["2026-11-26-caching/1331-auto.json", "2026-11-26-1236-claude_claude-haiku-5-5.json"]
    assert rows[0]["label"] == "Auto (Haiku → Opus)" and rows[0]["time"] == "13:31"
    assert rows[0]["cost_per_ticket"] == 0.002 and rows[1]["cost_per_ticket"] is None


def test_only_current_reports_can_be_read(tmp_path):
    root = folder(tmp_path)
    assert eval_results.report("2026-11-26-caching/1331-auto.json", root)["cases"][0]["ticket"] == "T-1001"
    for bad in ["archive/2026-11-20-0900-old.json", "../secret.json", "2026-11-26-sample.json", "nope.json"]:
        assert eval_results.report(bad, root) is None


def test_the_index_lists_every_current_run(tmp_path):
    text = eval_results.write_index(folder(tmp_path)).read_text()
    assert "[Haiku 5.5 only](2026-11-26-1236-claude_claude-haiku-5-5.json)" in text and "old.json" not in text


def test_the_api_serves_the_real_folder():
    client = TestClient(api.app)
    rows = client.get("/api/evals").json()
    assert rows and all(r["total"] for r in rows)
    assert client.get(f"/api/evals/{rows[0]['id']}").json()["cases"]
    assert client.get("/api/evals/archive/2026-11-23-1358-claude_claude-opus-5-5.json").status_code == 404
