"""Reviewer control experiment (ROADMAP v0.0, ADR-011).

Question: does a reviewer from a *different* model family catch more injected errors than a
reviewer from the *same* family as the worker that wrote the answer?

Two modes:

* ``static``  - every reviewer reviews the hand-written corpus in ``injected-errors/``.
                Measures raw reviewer capability per model. Does not test self-review bias.
* ``self``    - a worker model first rewrites each case's answer in its own words (grounded in
                the evidence packet, as structured claims). The script then injects errors
                programmatically. Every reviewer, including the worker itself, reviews the
                result. Comparing reviewer == worker family vs others is the actual test.

Model specs look like ``anthropic:claude-opus-5-5``, ``deepseek:deepseek-chat``,
``openai:gpt-5``, ``qwen:qwen3-max``, ``gemini:gemini-2.5-pro``, ``ollama:llama3.3``,
``openrouter:<vendor/model>`` or ``custom:<model>`` (``BQ_EVAL_BASE_URL`` + ``BQ_EVAL_API_KEY``).

Responses are cached under ``evals/results/cache`` so reruns and re-scoring are free.

Usage::

    uv sync --all-groups --extra eval
    export ANTHROPIC_API_KEY=... DEEPSEEK_API_KEY=...
    uv run python evals/reviewer_experiment.py static  --reviewer anthropic:claude-opus-5-5 --reviewer deepseek:deepseek-chat
    uv run python evals/reviewer_experiment.py self    --worker anthropic:claude-opus-5-5 \
        --reviewer anthropic:claude-opus-5-5 --reviewer deepseek:deepseek-chat --report docs/research/reviewer-experiment.md
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "injected-errors"
RESULTS_DIR = ROOT / "results"
CACHE_DIR = RESULTS_DIR / "cache"

ERROR_TYPES = [
    "value_mismatch",
    "fabricated_citation",
    "citation_unsupported",
    "execution_unsupported",
    "plan_incomplete",
    "stat_method",
    "overclaim_causal",
]

# --------------------------------------------------------------------------- providers

FAMILIES: dict[str, dict[str, Any]] = {
    "anthropic": {"sdk": "anthropic", "family": "anthropic"},
    "openai": {"sdk": "openai", "family": "openai", "base_url": None, "key_env": "OPENAI_API_KEY"},
    "deepseek": {
        "sdk": "openai",
        "family": "deepseek",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "sdk": "openai",
        "family": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "gemini": {
        "sdk": "openai",
        "family": "google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
    },
    "ollama": {
        "sdk": "openai",
        "family": "local",
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
    },
    "openrouter": {
        "sdk": "openai",
        "family": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
    },
    "custom": {
        "sdk": "openai",
        "family": "custom",
        "base_url": os.environ.get("BQ_EVAL_BASE_URL"),
        "key_env": "BQ_EVAL_API_KEY",
    },
}


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @classmethod
    def parse(cls, text: str) -> ModelSpec:
        provider, _, model = text.partition(":")
        if provider not in FAMILIES or not model:
            raise SystemExit(f"bad model spec {text!r}; providers: {', '.join(FAMILIES)}")
        return cls(provider, model)

    @property
    def family(self) -> str:
        return FAMILIES[self.provider]["family"]

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


class LLM:
    """Minimal JSON-returning chat client over the official Anthropic and OpenAI SDKs."""

    def __init__(self, spec: ModelSpec, cache: bool = True) -> None:
        self.spec = spec
        self.cache = cache
        info = FAMILIES[spec.provider]
        if info["sdk"] == "anthropic":
            import anthropic

            self._anthropic = anthropic.Anthropic()
            self._openai = None
        else:
            import openai

            key = os.environ.get(info["key_env"]) if info["key_env"] else "ollama"
            if not key:
                raise SystemExit(f"{info['key_env']} is not set (needed for {spec})")
            self._openai = openai.OpenAI(api_key=key, base_url=info["base_url"])
            self._anthropic = None

    def json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = hashlib.sha256(
            f"{self.spec}\x00{system}\x00{user}\x00{json.dumps(schema, sort_keys=True)}".encode()
        ).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        if self.cache and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        text = self._call(system, user, schema)
        data = _parse_json(text)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def _call(self, system: str, user: str, schema: dict[str, Any]) -> str:
        for attempt in range(4):
            try:
                if self._anthropic is not None:
                    resp = self._anthropic.messages.create(
                        model=self.spec.model,
                        max_tokens=16000,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        output_config={"format": {"type": "json_schema", "schema": schema}},
                    )
                    if resp.stop_reason == "refusal":
                        raise RuntimeError(f"refusal: {resp.stop_details}")
                    return next(b.text for b in resp.content if b.type == "text")
                assert self._openai is not None
                kwargs: dict[str, Any] = {}
                if attempt == 0:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = self._openai.chat.completions.create(
                    model=self.spec.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user + "\n\nReturn only JSON."},
                    ],
                    **kwargs,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc:
                wait = 2**attempt
                print(
                    f"  [{self.spec}] attempt {attempt + 1} failed: {type(exc).__name__}: {exc}; retrying in {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
        raise RuntimeError(f"{self.spec}: gave up after retries")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


# --------------------------------------------------------------------------- corpus


def load_corpus(limit: int | None = None) -> list[dict[str, Any]]:
    files = sorted(CORPUS_DIR.glob("ie-*.yaml"))
    cases = [yaml.safe_load(f.read_text(encoding="utf-8")) for f in files]
    return cases[:limit] if limit else cases


def render_evidence(ev: dict[str, Any]) -> str:
    out: list[str] = []
    runs = ev.get("runs") or []
    out.append("## Kernel runs (what was actually executed)")
    if not runs:
        out.append("(no code was executed)")
    for r in runs:
        out.append(
            f"### run {r['run_id']} — status: {r['status']}\n```python\n{r['code'].rstrip()}\n```\nstdout:\n```\n{(r.get('stdout') or '').rstrip()}\n```"
        )
    cits = ev.get("citations") or []
    out.append("## Available citations (the ONLY sources the answer may cite)")
    if not cits:
        out.append("(none)")
    for c in cits:
        out.append(
            f"[{c['key']}] {c['title']} ({c.get('authors', '')}, {c.get('year', '')}) doi:{c['doi']}\nAbstract: {c['abstract'].strip()}"
        )
    plan = ev.get("plan") or []
    out.append("## Approved plan")
    out.append("\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan)) if plan else "(no plan)")
    return "\n\n".join(out)


def render_claims(claims: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{c['index']}] ({c.get('kind', '?')}) {c['text'].strip()}" for c in claims)


# --------------------------------------------------------------------------- reviewer

REVIEW_SYSTEM = """You are a meticulous scientific reviewer checking an AI assistant's answer against the ONLY evidence it had.
Flag every claim that is not supported by the evidence. Error types:
- value_mismatch: a number differs from the run output or the cited abstract (including wrong unit or wrong group).
- fabricated_citation: cites a key that does not exist, or attributes a finding to a citation about something else.
- citation_unsupported: the citation exists and is on topic but does not support this specific statement.
- execution_unsupported: claims a computation/analysis was done but there is no successful run that did it.
- plan_incomplete: implies the approved plan is complete while a step was skipped.
- stat_method: a statistical error visible in the code/output (no multiple-testing correction, n too small, etc.).
- overclaim_causal: evidence is associative but the claim uses causal language.
Only flag real problems. A correct claim must NOT be flagged. Be precise about which claim index."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_index": {"type": "integer"},
                    "type": {"type": "string", "enum": ERROR_TYPES},
                    "explanation": {"type": "string"},
                },
                "required": ["claim_index", "type", "explanation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def review(
    llm: LLM, case: dict[str, Any], claims: list[dict[str, Any]], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    user = (
        f"# User question\n{case['question'].strip()}\n\n# Evidence\n{render_evidence(evidence)}\n\n"
        f"# The assistant's answer, split into numbered claims\n{render_claims(claims)}\n\n"
        "List every unsupported claim as a finding. If everything is supported, return an empty list."
    )
    data = llm.json(REVIEW_SYSTEM, user, REVIEW_SCHEMA)
    findings = [f for f in data.get("findings", []) if isinstance(f, dict) and "claim_index" in f]
    for f in findings:
        if f.get("type") not in ERROR_TYPES:
            f["type"] = "unknown"
    return findings


# --------------------------------------------------------------------------- self mode: worker + injection

WORKER_SYSTEM = """You are a careful biomedical research assistant. Answer the user's question using ONLY the evidence packet.
Write the answer as a list of 5 to 9 atomic claims. Each claim is one sentence. Ground numbers in run outputs or abstracts,
cite with the given keys like [c1], and refer to runs you rely on. Do not invent anything not in the evidence."""

WORKER_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["numeric", "citation", "execution", "causal", "plan"],
                    },
                    "citation_keys": {"type": "array", "items": {"type": "string"}},
                    "run_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "kind", "citation_keys", "run_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)(?![\w.]*\])")


def worker_answer(llm: LLM, case: dict[str, Any]) -> list[dict[str, Any]]:
    user = f"# User question\n{case['question'].strip()}\n\n# Evidence packet\n{render_evidence(case['evidence'])}"
    data = llm.json(WORKER_SYSTEM, user, WORKER_SCHEMA)
    claims = []
    for i, c in enumerate(data.get("claims", []), start=1):
        claims.append(
            {
                "index": i,
                "text": c["text"],
                "kind": c.get("kind", "numeric"),
                "citation_keys": c.get("citation_keys", []),
                "run_ids": c.get("run_ids", []),
            }
        )
    return claims


def inject(
    claims: list[dict[str, Any]], evidence: dict[str, Any], rng: random.Random, n_errors: int
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Return (mutated claims, mutated evidence, injected error list). Deterministic given rng."""
    claims = copy.deepcopy(claims)
    evidence = copy.deepcopy(evidence)
    errors: list[dict[str, Any]] = []
    available = list(range(len(claims)))
    rng.shuffle(available)
    cit_keys = {c["key"] for c in evidence.get("citations") or []}
    run_ids = {r["run_id"] for r in evidence.get("runs") or []}
    for idx in available:
        if len(errors) >= n_errors:
            break
        c = claims[idx]
        options = []
        if _NUM.search(c["text"]):
            options.append("value_mismatch")
        if any(k in cit_keys for k in c["citation_keys"]) or re.search(r"\[c\d+\]", c["text"]):
            options.append("fabricated_citation")
        if c["run_ids"] and set(c["run_ids"]) & run_ids and len(run_ids) > 1:
            options.append("execution_unsupported")
        if not options:
            continue
        kind = rng.choice(options)
        if kind == "value_mismatch":
            m = _NUM.search(c["text"])
            assert m
            old = m.group(0)
            value = float(old.replace(",", ""))
            factor = rng.choice([0.5, 1.5, 2, 3])
            new_value = value * factor
            new = (
                f"{new_value:,.0f}"
                if "." not in old and "," in old
                else (f"{new_value:.2f}" if "." in old else str(int(new_value)))
            )
            c["text"] = c["text"][: m.start()] + new + c["text"][m.end() :]
            errors.append(
                {"claim_index": c["index"], "type": kind, "description": f"{old} -> {new}"}
            )
        elif kind == "fabricated_citation":
            fake = f"c{len(cit_keys) + 5}"
            existing = re.findall(r"\[(c\d+)\]", c["text"])
            if existing:
                c["text"] = c["text"].replace(f"[{existing[0]}]", f"[{fake}]", 1)
            else:
                c["text"] = c["text"].rstrip(".") + f" [{fake}]."
            errors.append(
                {
                    "claim_index": c["index"],
                    "type": kind,
                    "description": f"cites nonexistent [{fake}]",
                }
            )
        else:
            target = next(r for r in c["run_ids"] if r in run_ids)
            evidence["runs"] = [r for r in evidence["runs"] if r["run_id"] != target]
            run_ids.discard(target)
            errors.append(
                {
                    "claim_index": c["index"],
                    "type": kind,
                    "description": f"run {target} removed from evidence",
                }
            )
    return claims, evidence, errors


# --------------------------------------------------------------------------- scoring


@dataclass
class Score:
    reviewer: str
    same_family_as_worker: bool | None
    cases: int = 0
    injected: int = 0
    hit_strict: int = 0  # right claim and right type
    hit_lenient: int = 0  # right claim, any type
    findings: int = 0
    false_positives: int = 0  # findings on claims without an injected error
    clean_cases: int = 0
    clean_cases_flagged: int = 0
    by_type: dict[str, list[int]] = field(
        default_factory=lambda: {t: [0, 0] for t in ERROR_TYPES}
    )  # type -> [hits, total]

    def add(self, injected: list[dict[str, Any]], findings: list[dict[str, Any]]) -> None:
        self.cases += 1
        self.injected += len(injected)
        self.findings += len(findings)
        bad_idx = {e["claim_index"] for e in injected}
        if not injected:
            self.clean_cases += 1
            if findings:
                self.clean_cases_flagged += 1
        for e in injected:
            self.by_type.setdefault(e["type"], [0, 0])[1] += 1
            matching = [f for f in findings if f["claim_index"] == e["claim_index"]]
            if matching:
                self.hit_lenient += 1
            if any(f["type"] == e["type"] for f in matching):
                self.hit_strict += 1
                self.by_type[e["type"]][0] += 1
        self.false_positives += sum(1 for f in findings if f["claim_index"] not in bad_idx)

    def summary(self) -> dict[str, Any]:
        def pct(a: int, b: int) -> float | None:
            return round(100 * a / b, 1) if b else None

        return {
            "reviewer": self.reviewer,
            "same_family_as_worker": self.same_family_as_worker,
            "cases": self.cases,
            "injected_errors": self.injected,
            "recall_strict_pct": pct(self.hit_strict, self.injected),
            "recall_lenient_pct": pct(self.hit_lenient, self.injected),
            "precision_pct": pct(self.findings - self.false_positives, self.findings),
            "false_positives": self.false_positives,
            "clean_cases_flagged": f"{self.clean_cases_flagged}/{self.clean_cases}",
            "recall_by_type_pct": {t: pct(h, n) for t, (h, n) in self.by_type.items() if n},
        }


# --------------------------------------------------------------------------- main


def run(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_corpus(args.limit)
    reviewers = [ModelSpec.parse(r) for r in args.reviewer]
    worker = ModelSpec.parse(args.worker) if getattr(args, "worker", None) else None
    llms = {str(s): LLM(s, cache=not args.no_cache) for s in reviewers}
    if worker and str(worker) not in llms:
        llms[str(worker)] = LLM(worker, cache=not args.no_cache)

    prepared: list[dict[str, Any]] = []
    for case in cases:
        if args.mode == "static":
            prepared.append(
                {
                    "id": case["id"],
                    "case": case,
                    "claims": case["claims"],
                    "evidence": case["evidence"],
                    "injected": case.get("injected_errors") or [],
                }
            )
        else:
            assert worker is not None
            print(f"worker {worker} answering {case['id']}", file=sys.stderr)
            claims = worker_answer(llms[str(worker)], case)
            rng = random.Random(f"{args.seed}:{case['id']}")
            n_errors = 0 if case["id"] in args.clean_ids else rng.choice([1, 1, 2])
            mclaims, mevidence, injected = inject(claims, case["evidence"], rng, n_errors)
            prepared.append(
                {
                    "id": case["id"],
                    "case": case,
                    "claims": mclaims,
                    "evidence": mevidence,
                    "injected": injected,
                    "worker_claims": claims,
                }
            )

    scores = {
        str(s): Score(str(s), (s.family == worker.family) if worker else None) for s in reviewers
    }
    details: list[dict[str, Any]] = []
    for item in prepared:
        for spec in reviewers:
            print(f"reviewer {spec} on {item['id']}", file=sys.stderr)
            findings = review(llms[str(spec)], item["case"], item["claims"], item["evidence"])
            scores[str(spec)].add(item["injected"], findings)
            details.append(
                {
                    "case": item["id"],
                    "reviewer": str(spec),
                    "injected": item["injected"],
                    "findings": findings,
                }
            )

    result = {
        "mode": args.mode,
        "worker": str(worker) if worker else None,
        "reviewers": [str(s) for s in reviewers],
        "seed": args.seed,
        "n_cases": len(prepared),
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scores": [scores[str(s)].summary() for s in reviewers],
        "details": details,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.mode}-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out}", file=sys.stderr)
    return result


def to_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Reviewer experiment — mode `{result['mode']}`",
        "",
        f"Ran: {result['ran_at']} · cases: {result['n_cases']} · worker: `{result['worker']}` · seed: {result['seed']}",
        "",
        "| reviewer | same family as worker | injected | recall (strict) | recall (lenient) | precision | false positives | clean cases flagged |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in result["scores"]:
        lines.append(
            f"| `{s['reviewer']}` | {s['same_family_as_worker']} | {s['injected_errors']} | {s['recall_strict_pct']} | {s['recall_lenient_pct']} | {s['precision_pct']} | {s['false_positives']} | {s['clean_cases_flagged']} |"
        )
    lines += [
        "",
        "## Recall by error type (strict, %)",
        "",
        "| reviewer | " + " | ".join(ERROR_TYPES) + " |",
        "|---|" + "---|" * len(ERROR_TYPES),
    ]
    for s in result["scores"]:
        bt = s["recall_by_type_pct"]
        lines.append(
            f"| `{s['reviewer']}` | " + " | ".join(str(bt.get(t, "-")) for t in ERROR_TYPES) + " |"
        )
    lines += [
        "",
        "Strict = right claim and right error type; lenient = right claim, any type. Precision = findings on genuinely bad claims / all findings.",
        "In `self` mode only `value_mismatch`, `fabricated_citation` and `execution_unsupported` are injected (programmatically), so other columns are `-`.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="mode", required=True)
    for mode in ("static", "self"):
        sp = sub.add_parser(mode)
        sp.add_argument("--reviewer", action="append", required=True, help="model spec, repeatable")
        sp.add_argument("--limit", type=int, default=None)
        sp.add_argument("--seed", default="bq-v0")
        sp.add_argument("--no-cache", action="store_true")
        sp.add_argument("--report", type=Path, default=None, help="write a Markdown summary here")
        if mode == "self":
            sp.add_argument("--worker", required=True, help="model whose answers get reviewed")
            sp.add_argument(
                "--clean-ids",
                nargs="*",
                default=["ie-021", "ie-022", "ie-023", "ie-024"],
                help="cases kept error-free as controls",
            )
    args = p.parse_args(argv)
    result = run(args)
    md = to_markdown(result)
    print(md)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(md, encoding="utf-8")
        print(f"wrote {args.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
