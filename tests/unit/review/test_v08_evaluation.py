"""Checks for the frozen scenario bank and evidence-coverage scoring."""

from importlib import import_module
from pathlib import Path

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[3] / "evals"))
    return import_module("run")


def test_fixed_splits_and_old_questions_are_development_only(runner):
    bank = runner.load_cases()
    assert len({case.id for case in bank}) == 90
    for scenario in "ABC":
        rows = [case for case in bank if case.scenario == scenario]
        assert sum(case.split == "heldout" for case in rows) == 10
        assert sum(case.split == "development" for case in rows) == 20
        assert all(case.split == "development" for case in rows if int(case.id[-2:]) <= 5)
        assert all(len(case.criteria) >= 2 and all(case.criteria.values()) for case in rows)


def test_scoring_requires_a_verbatim_passage_per_covered_category(runner):
    criteria = {"direction": "Reports direction", "scope": "Limits scope"}
    answer = "Only paired donors were analyzed."
    result = runner.score_categories(criteria, answer, {"direction": None, "scope": answer})
    assert result == (1, 2)
    for invalid in (
        {"scope": answer},
        {"direction": None, "scope": "invented"},
        {"direction": "", "scope": answer},
    ):
        with pytest.raises(ValueError):
            runner.score_categories(criteria, answer, invalid)


def test_incomplete_runs_are_visible_and_unknown_cost_is_not_free(runner):
    rows = [
        {
            "status": "ok",
            "covered": 2,
            "expected": 3,
            "claims": {"reviewed": 2},
            "cost_usd": None,
            "elapsed_s": 8,
        },
        {
            "status": "failed",
            "covered": None,
            "expected": 2,
            "claims": {},
            "cost_usd": 0.4,
            "elapsed_s": 2,
        },
    ]
    result = runner.aggregate(rows)
    assert result["coverage"] is None
    assert result["scored_coverage"] == pytest.approx(2 / 3)
    assert result["grounded_fraction"] == 1
    assert result["cost_usd"] is None
    assert result["elapsed_s"] == 10
    assert result["failed"] == 1


def test_durable_budget_keeps_uncertain_charges_and_serializes_reservations(tmp_path, runner):
    budget = import_module("v08_budget")

    path = tmp_path / "charges.sqlite"
    one, two = budget.SpendingLog(path, 100), budget.SpendingLog(path, 100)
    token = one.reserve(80)
    with pytest.raises(budget.SpendingLimitError):
        two.reserve(21)
    one.settle(token, 12)
    two.reserve(88)
    with pytest.raises(budget.SpendingLimitError):
        budget.SpendingLog(path, 100).reserve(1)


def test_omics_inputs_are_stable_and_different_across_cases(tmp_path, runner):
    first = runner.stage_cells(tmp_path, 21).read_bytes()
    assert first == runner.stage_cells(tmp_path, 21).read_bytes()
    assert first != runner.stage_cells(tmp_path, 22).read_bytes()


@pytest.mark.anyio
@pytest.mark.parametrize("profile", ["minimal", "current", "confirmation"])
@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6.2-sol"])
async def test_case_through_responses_adapter_offline(
    tmp_path, runner, monkeypatch, profile, model
):
    import json
    from argparse import Namespace

    import httpx2

    budget = import_module("v08_budget")

    from bioquorum.application.workspace import Workspace
    from bioquorum.runtime.sandbox import Isolation

    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(Workspace, "kernel_isolation", lambda self: Isolation.SEATBELT)
    sent = []
    case = runner.Case(
        "q-B-21", "B", "heldout", "Check the paired design.", {"design": "Use paired donors"}
    )

    async def respond(request):
        body = json.loads(request.content)
        assert body["model"] == model
        if request.url.path == "/v1/responses/input_tokens":
            assert "max_output_tokens" not in body and "input" in body
            return httpx2.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 100}
            )
        sent.append(body)
        text = request.content.decode()
        if "Grade scientific coverage" in text:
            answer = '{"design":null}'
        elif "atomic, checkable claims" in text:
            answer = '{"claims":[{"text":"Pairing remains unknown.","kind":"plan"}]}'
        else:
            answer = "Pairing remains unknown."
        return httpx2.Response(
            200,
            json={
                "id": "resp_example",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": model,
                "output": [
                    {
                        "id": "msg_example",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": answer, "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            },
        )

    meter = budget.MeteredOpenAI(
        budget.SpendingLog(tmp_path / "charges.sqlite", 20_000_000),
        transport=httpx2.MockTransport(respond),
        model=model,
    )
    async with httpx2.AsyncClient(transport=meter) as http:
        row = await runner.run_case(
            case,
            profile,
            tmp_path,
            Namespace(worker="openai:" + model, reviewer="openai:" + model),
            meter,
            http,
            http,
        )
    assert row["status"] == "ok", row
    assert row["covered"] == 0
    assert row["claims"] == {"unverified": 1}
    assert len(sent) >= 3
    assert all(
        p["max_output_tokens"] == budget.MAX_OUTPUT and p["service_tier"] == "default" for p in sent
    )
    names = {t["name"] for t in sent[0]["tools"]}
    assert (names == {"python_execute"}) == (profile == "minimal")
    assert "Use paired donors" not in str(sent[0])
    assert 0 < meter.reserved_usd < 0.01


def test_confirmed_bank_digest(runner):
    from dataclasses import asdict

    from bioquorum.core.hashing import canonical_json, sha256_bytes

    digest = sha256_bytes(canonical_json([asdict(case) for case in runner.load_cases()]))
    assert digest == "a1ac65f6799d7c0a040339feb868d732e3920ede34c8e6f081be42355d8b269d"


def test_ablation_schedule_is_paired_and_excludes_comparison_tasks(runner):
    pairs = runner.ablation_schedule(runner.load_cases())
    assert len(pairs) == len({(case.id, arm) for case, arm in pairs})
    assert all(case.split == "heldout" and not case.id.endswith(("29", "30")) for case, _ in pairs)
    assert [(c.id, p) for c, p in pairs[:3]] == [
        ("q-A-25", "coverage"),
        ("q-B-25", "coverage"),
        ("q-C-25", "coverage"),
    ]
    assert all(p in runner.ABLATIONS for _, p in pairs)


@pytest.mark.anyio
async def test_ambiguous_call_retains_only_exact_input_and_bounded_output(tmp_path, runner):
    import sqlite3

    import httpx2

    budget = import_module("v08_budget")
    calls = []

    async def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/input_tokens"):
            return httpx2.Response(
                200, json={"object": "response.input_tokens", "input_tokens": 500}
            )
        raise httpx2.ReadTimeout("Timeout")

    ledger = budget.SpendingLog(tmp_path / "charges", 100_000)
    meter = budget.MeteredOpenAI(ledger, transport=httpx2.MockTransport(respond))
    request = httpx2.Request(
        "POST",
        "https://api.openai.com/v1/responses",
        json={"model": budget.MODEL, "input": "x", "max_output_tokens": budget.MAX_OUTPUT},
    )
    with pytest.raises(httpx2.ReadTimeout):
        await meter.handle_async_request(request)
    assert calls == ["/v1/responses/input_tokens", "/v1/responses"]
    with sqlite3.connect(ledger.path) as db:
        assert 45000 < db.execute("select sum(amount) from charges").fetchone()[0] < 100_000


def test_report_counts_absent_questions_and_keeps_incomplete_coverage_unknown(tmp_path, runner):
    from importlib import import_module

    report = import_module("v08_report")
    case = runner.load_cases()[-1]
    row = {
        "status": "ok",
        "expected": 2,
        "covered": 1,
        "claims": {"grounded": 1},
        "elapsed_s": 12,
        "cost_usd": 0.2,
    }
    directory = tmp_path / case.split / "minimal" / case.id
    directory.mkdir(parents=True)
    runner.write_json(directory / "result.json", row)
    result = report.baseline(tmp_path, [case])
    assert result["minimal"]["coverage"] == 0.5
    assert result["current"]["coverage"] is None
    assert result["current"]["missing"] == 1
    assert result["current"]["cost_usd"] is None


@pytest.mark.anyio
async def test_count_failure_stops_before_inference_or_reservation(tmp_path, runner):
    import sqlite3

    import httpx2

    budget = import_module("v08_budget")

    def reject(request):
        assert request.url.path.endswith("/input_tokens")
        return httpx2.Response(403, json={"error": {"message": "unavailable"}})

    ledger = budget.SpendingLog(tmp_path / "charges", 20_000_000)
    meter = budget.MeteredOpenAI(ledger, transport=httpx2.MockTransport(reject))
    request = httpx2.Request(
        "POST",
        "https://api.openai.com/v1/responses",
        json={"model": budget.MODEL, "input": "x", "max_output_tokens": budget.MAX_OUTPUT},
    )
    with pytest.raises(ValueError):
        await meter.handle_async_request(request)
    assert meter.preflight_error == "HTTP 403"
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("select count(*) from charges").fetchone()[0] == 0


def test_budget_cli_requires_explicit_opt_in_above_twenty(runner):
    parser = runner.argument_parser()
    assert parser.parse_args([]).max_usd == 20
    assert parser.parse_args(["--max-usd", "50"]).max_usd == 50
    for bad in ("0", "53"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--max-usd", bad])


@pytest.mark.anyio
@pytest.mark.parametrize("profile", ["baseline", "ablations", "current", "minimal"])
async def test_extra_budget_restricted_to_confirmation(runner, tmp_path, monkeypatch, profile):
    monkeypatch.setattr(runner, "ROOT", tmp_path / "results")
    args = runner.argument_parser().parse_args(["--profile", profile, "--max-usd", "52"])
    with pytest.raises(ValueError, match="confirmation"):
        await runner.main(args)
    assert not runner.ROOT.exists()


def test_ablation_report_keeps_finished_arms_awaiting_their_controls(tmp_path, runner):
    report = import_module("v08_report")
    cases = [c for c in runner.load_cases() if c.id in {"q-A-25", "q-B-25", "q-C-25"}]
    for case, status in zip(cases, ("ok", "failed", "missing"), strict=True):
        if status == "missing":
            continue
        directory = tmp_path / "heldout" / "coverage" / case.id
        directory.mkdir(parents=True)
        runner.write_json(directory / "result.json", {"status": status})
    result = report.paired(tmp_path, cases)["coverage"]
    assert result["progress"] == {"completed": 1, "incomplete": 1, "missing": 1, "paired": 0}
    assert result["on"]["coverage"] is None


@pytest.mark.anyio
@pytest.mark.parametrize("jobs", [2, 4])
async def test_campaign_runs_bounded_independent_sessions_and_reuses_results(
    runner, tmp_path, monkeypatch, jobs
):
    import asyncio

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    active = peak = 0
    launched = []
    meters = {}
    both_started = asyncio.Event()

    async def simulate(case, profile, directory, args, meter, http, public):
        nonlocal active, peak
        launched.append((case.id, profile))
        active += 1
        peak = max(peak, active)
        if active == jobs:
            both_started.set()
        assert id(meter) not in meters
        meters[id(meter)] = meter
        try:
            await asyncio.wait_for(both_started.wait(), timeout=2)
            await asyncio.sleep(0)
        finally:
            active -= 1
            del meters[id(meter)]
        return {
            "id": case.id,
            "profile": profile,
            "status": "ok",
            "expected": 2,
            "covered": 1,
            "claims": {},
            "cost_usd": 0,
            "elapsed_s": 1,
        }

    monkeypatch.setattr(runner, "run_case", simulate)
    parser = runner.argument_parser()
    assert parser.parse_args([]).jobs == 4
    args = parser.parse_args(
        ["--profile", "baseline", "--case", "q-A-21", "--case", "q-A-22", "--jobs", str(jobs)]
    )
    await runner.main(args)
    await runner.main(args)
    assert peak == jobs and len(set(launched)) == len(launched) == 4
    assert len(list(tmp_path.glob("heldout/*/*/result.json"))) == 4


@pytest.mark.anyio
async def test_parallel_budget_waits_for_inflight_settlement(tmp_path, runner):
    import asyncio

    budget = import_module("v08_budget")
    ledger = budget.SpendingLog(tmp_path / "charges", 100)
    attempted = asyncio.Event()

    async def another():
        attempted.set()
        async with ledger.reservation(80) as token:
            ledger.settle(token, 11)

    async with ledger.reservation(80) as token:
        task = asyncio.create_task(another())
        await attempted.wait()
        assert not task.done()
        ledger.settle(token, 12)
    await asyncio.wait_for(task, timeout=2)
    with pytest.raises(budget.SpendingLimitError):
        async with ledger.reservation(78):
            pytest.fail("Existing charges must survive both settlements")


@pytest.mark.anyio
async def test_count_rate_limit_retries_without_charging_the_preflight(tmp_path, runner):
    import sqlite3

    import httpx2

    budget = import_module("v08_budget")
    calls = []

    def transient(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx2.Response(429, headers={"retry-after": "0"})
        if request.url.path.endswith("/input_tokens"):
            return httpx2.Response(200, json={"input_tokens": 100})
        return httpx2.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 10}})

    ledger = budget.SpendingLog(tmp_path / "charges", 200_000)
    meter = budget.MeteredOpenAI(ledger, transport=httpx2.MockTransport(transient))
    async with httpx2.AsyncClient(transport=meter) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            json={"model": budget.MODEL, "input": "x", "max_output_tokens": budget.MAX_OUTPUT},
        )
        assert response.is_success
    assert calls == ["/v1/responses/input_tokens"] * 2 + ["/v1/responses"]
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("select count(*),sum(amount) from charges").fetchone() == (1, 410)


@pytest.mark.anyio
async def test_development_model_isolation_and_frozen_heldout(tmp_path, runner, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    calls = []

    async def simulate(case, profile, directory, args, meter, http, public):
        calls.append((args.worker, args.reviewer, meter.model, meter.ledger.path))
        return dict(status="ok", expected=1, covered=1, claims={}, elapsed_s=1, cost_usd=0)

    monkeypatch.setattr(runner, "run_case", simulate)
    parser = runner.argument_parser()
    dev = ["--split", "development", "--profile", "current", "--case", "q-A-06"]
    heldout = ["--profile", "current", "--case", "q-A-21"]
    legacy = "openai:gpt-6.1-sol"
    latest = "openai:gpt-6.2-sol"
    for flags in (dev, [*dev, "--worker", legacy], dev, heldout):
        await runner.main(parser.parse_args(flags))
    assert calls == [
        (spec, spec, spec.split(":")[1], tmp_path / "spending.sqlite")
        for spec in [latest, legacy, legacy]
    ]
    assert (tmp_path / "heldout/current/q-A-21/result.json").is_file()
    assert (tmp_path / "development/current/q-A-06/result.json").is_file()
    with pytest.raises(ValueError, match="heldout"):
        await runner.main(parser.parse_args([*heldout, "--worker", latest]))


@pytest.mark.anyio
@pytest.mark.parametrize("failures", [1, 3])
@pytest.mark.parametrize("failure", ["connect", "read_headers", "read_body"])
async def test_token_count_network_failure(tmp_path, runner, monkeypatch, failures, failure):
    import sqlite3
    from unittest.mock import AsyncMock

    import httpx2

    budget = import_module("v08_budget")
    monkeypatch.setattr(budget.asyncio, "sleep", AsyncMock())
    calls = []
    closed = []

    error = httpx2.ConnectError if failure == "connect" else httpx2.ReadTimeout

    class TimeoutBody(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            raise httpx2.ReadTimeout("interrupted count")

        async def aclose(self):
            closed.append(True)

    def respond(request):
        calls.append(request.url.path)
        if len(calls) <= failures:
            if failure == "read_body":
                return httpx2.Response(200, stream=TimeoutBody())
            raise error("interrupted count")
        if request.url.path.endswith("/input_tokens"):
            return httpx2.Response(200, json={"input_tokens": 100})
        return httpx2.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 10}})

    ledger = budget.SpendingLog(tmp_path / "charges", 200_000)
    meter = budget.MeteredOpenAI(ledger, transport=httpx2.MockTransport(respond))
    body = {"model": budget.MODEL, "input": "x", "max_output_tokens": budget.MAX_OUTPUT}
    async with httpx2.AsyncClient(transport=meter) as client:
        if failures == 3:
            with pytest.raises(error):
                await client.post("https://api.openai.com/v1/responses", json=body)
            assert meter.preflight_error == error.__name__
        else:
            assert (await client.post("https://api.openai.com/v1/responses", json=body)).is_success
    assert calls == ["/v1/responses/input_tokens"] * min(failures + 1, 3) + (
        ["/v1/responses"] if failures == 1 else []
    )
    if failure == "read_body":
        assert len(closed) == failures
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("select count(*) from charges").fetchone()[0] == int(failures == 1)


@pytest.mark.anyio
async def test_confirmation_uses_frozen_cases_and_separate_results(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    launched = []
    expected = {f"q-{s}-{n}" for s in "ABC" for n in (23, 24)}

    async def simulate(case, profile, directory, args, meter, http, public):
        assert profile == "confirmation"
        assert meter.ledger.limit == 52_000_000
        assert args.worker == args.reviewer == runner.SPEC
        assert meter.ledger.path == tmp_path / "spending.sqlite"
        launched.append(case.id)
        return dict(
            id=case.id, status="ok", expected=2, covered=1, claims={}, elapsed_s=1, cost_usd=0
        )

    monkeypatch.setattr(runner, "run_case", simulate)
    parser = runner.argument_parser()
    cases = [c for c in runner.load_cases() if c.split == "heldout"]
    for c in cases:
        if c.id in expected:
            directory = tmp_path / "heldout" / "current" / c.id
            directory.mkdir(parents=True)
            runner.write_json(
                directory / "result.json",
                dict(
                    id=c.id, status="ok", expected=2, covered=1, claims={}, elapsed_s=2, cost_usd=0
                ),
            )
    for _ in range(2):
        await runner.main(parser.parse_args(["--profile", "confirmation", "--max-usd", "52"]))
    assert set(launched) == expected and len(launched) == 6
    report = import_module("v08_report").confirmation(tmp_path, cases)
    assert set(report["ids"]) == expected and report["progress"]["paired"] == 6
    for flags in (["--split", "development"], ["--case", "q-A-29"]):
        with pytest.raises(ValueError):
            await runner.main(parser.parse_args(["--profile", "confirmation", *flags]))


def test_released_profile_matches_the_frozen_candidate_and_persists(runner, tmp_path, monkeypatch):
    from bioquorum.application import settings, workspace
    from bioquorum.application.controls import ResearchControls

    assert runner.sha256_bytes(runner.canonical_json(runner.CANDIDATE_AGENT)) == (
        "4d4d96b4e1cd651b5d318e55c032f9dcc16c102c08f312f868fbfc7ba9aa4d19"
    )
    with monkeypatch.context() as prior_release:
        prior_release.setattr(workspace, "RECOMMENDED", None)
        legacy = workspace.init_project(tmp_path / "legacy")
    project = workspace.init_project(tmp_path / "candidate")
    assert project.profile_name == "recommended"
    assert project.config["agent"] == {**runner.CANDIDATE_AGENT, "program_context": False}
    assert workspace.load(project.root).agent_options() == legacy.agent_options()
    assert workspace.load(legacy.root).profile_name == "custom"
    controls = ResearchControls(project)
    snapshot = settings.snapshot(controls)
    for name, binding in [("minimal", False), ("recommended", True)]:
        snapshot = settings.save(
            controls,
            settings.SettingsEdit(revision=snapshot["revision"], patch={"profile": {"name": name}}),
        )
        assert snapshot["profile"]["name"] == name
        assert workspace.load(project.root).agent_options().binding is binding
    snapshot = settings.save(
        controls,
        settings.SettingsEdit(revision=snapshot["revision"], patch={"agent": {"coverage": True}}),
    )
    assert snapshot["profile"]["name"] == "custom"
    assert workspace.load(project.root).agent_options().coverage
