from __future__ import annotations

import asyncio

from tools.eval_judge_v1 import main


def test_eval_judge_v1_dry_run_all_cases_pass() -> None:
    async def scenario() -> None:
        report = await main(dry_run=True)
        aggregate = report["aggregate"]
        assert aggregate["total_cases"] == 5
        assert aggregate["passed_cases"] == aggregate["total_cases"]
        assert aggregate["degraded_turns"] == 0
        assert aggregate["format_adherence"] == 1.0

    asyncio.run(scenario())
