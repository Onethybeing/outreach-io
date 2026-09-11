"""Score the active parse_resume prompt against hand-labelled resumes.

    .venv/Scripts/python scripts/eval_parse_resume.py [cases_dir] [--langfuse]

Each case in cases_dir (default evals/parse_resume/cases) is a JSON file, e.g.:
    {"resume_path": "example_cv/sourav_resume.pdf",
     "expected": {"name": "…", "current_role": "…", "skills": ["Python", "…"], "target_titles": ["…"]}}
Uses real Groq calls (one per case). With --langfuse, each case's score is sent as a Langfuse score.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm, prompts, telemetry  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.discovery.extract import extract_text  # noqa: E402
from app.parse_eval import load_cases, score_case  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cases_dir = Path(args[0] if args else "evals/parse_resume/cases")
    cases = load_cases(cases_dir)
    if not cases:
        raise SystemExit(f"No *.json cases in {cases_dir}")

    with SessionLocal() as db:
        prompt = prompts.get_active(db, "parse_resume")
        client = telemetry.langfuse_client(db) if "--langfuse" in sys.argv else None
        results = []
        for name, case in cases:
            text, _ = extract_text(db, case["resume_path"])
            rendered = prompts.render("parse_resume", prompt.template, {"resume_text": text[:12_000]})
            actual = llm.complete_json(db, prompt.model, rendered, prompt.temperature, name="parse_resume_eval")
            result = score_case(case["expected"], actual)
            results.append((name, result))
            if client:
                trace_id = telemetry.trace_id_for(f"parse-eval:{name}:{prompt.id}")
                telemetry.score(client, trace_id, "parse_accuracy", result["overall"] or 0.0,
                                comment=f"{name} · prompt v{prompt.version} · {json.dumps(result['fields'])}")
        telemetry.flush(client)

    print(f"parse_resume prompt v{prompt.version} ({prompt.model})")
    for name, result in results:
        fields = "  ".join(f"{k}={v:.2f}" for k, v in result["fields"].items())
        print(f"  {name:30} overall={result['overall']:.2f}  {fields}")
    scored = [r["overall"] for _, r in results if r["overall"] is not None]
    print(f"mean overall: {sum(scored) / len(scored):.3f} over {len(scored)} case(s)")


if __name__ == "__main__":
    main()
