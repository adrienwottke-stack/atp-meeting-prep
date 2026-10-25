"""Evaluate structural and coverage evidence; never equate coverage with factual truth."""

import argparse
import json
import statistics
from pathlib import Path

import pymupdf

from atp.models import BriefingResult


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    rows = []
    for run in report["runs"]:
        file = root / f"briefing-{run['index']}.json"
        if not file.exists():
            active = run.get("usage", {}).get("active_seconds")
            rows.append(
                {
                    "index": run["index"],
                    "company": run["company"],
                    "status": run["status"],
                    "briefing": False,
                    "wall_seconds": run.get("wall_seconds"),
                    "active_seconds": active,
                    "target_180": active is not None and active <= 180,
                    "deadline_300": active is not None and active <= 300,
                    "events": run.get("events", []),
                    "usage": run.get("usage", {}),
                }
            )
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        api_metadata = {"decision", "pdf_status", "pdf_path", "pdf_error"}
        result = BriefingResult.model_validate({k: v for k, v in data.items() if k not in api_metadata})
        doc = pymupdf.open(root / f"briefing-{run['index']}.pdf")
        text = "\n".join(page.get_text() for page in doc)
        rows.append(
            {
                "index": run["index"],
                "company": result.input.company.name,
                "status": result.status,
                "fixture_mode": result.fixture_mode,
                "wall_seconds": run["wall_seconds"],
                "active_seconds": result.usage.active_seconds,
                "target_180": result.usage.active_seconds <= 180,
                "deadline_300": result.usage.active_seconds <= 300,
                "schema_valid": True,
                "company_facts": sum(
                    f.subject_ref == "company" and f.status == "verified" for f in result.facts
                ),
                "facts": len(result.facts),
                "sources": len(result.sources),
                "people": [
                    {"name": p.name, "status": p.status, "career": len(p.career), "topics": len(p.topics)}
                    for p in result.people
                ],
                "questions": len(result.questions),
                "story_phases": len(result.storyline),
                "icp": result.icp.status,
                "recommendation": result.recommendation.value,
                "pdf_pages": len(doc),
                "pdf_contains_company_and_people": all(
                    n in text for n in [result.input.company.name, *[p.name for p in result.people]]
                ),
                "model_requests": result.usage.model_requests,
                "reserved_search_calls": result.usage.search_calls,
                "observed_search_calls": result.usage.observed_search_calls,
                "evidence_fetches": result.usage.evidence_fetches,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            }
        )
        for page_index in {0, len(doc) - 1}:
            doc[page_index].get_pixmap().save(
                str(root / f"briefing-{run['index']}-page-{page_index + 1}.png")
            )
    times = [row["active_seconds"] for row in rows if "active_seconds" in row]
    evaluation = {
        "runs": rows,
        "active_seconds": {"min": min(times), "median": statistics.median(times), "max": max(times)},
        "series_size": len(rows),
        "five_run_series": len(rows) == 5,
        "all_target_180": bool(rows) and all(r.get("target_180", False) for r in rows),
        "all_deadline_300": bool(rows) and all(r.get("deadline_300", False) for r in rows),
        "completed_briefings": sum(r["status"] == "completed" for r in rows),
        "partial_briefings": sum(r["status"] == "partial" for r in rows),
        "failed_runs": sum(r["status"] == "failed" for r in rows),
        "note": "Structure and coverage checks only; source truth and usefulness require manual review.",
    }
    (root / "evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(evaluation, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
