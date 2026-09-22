"""Run a fresh SA-04 repair round through cc-switch's notes=GPT provider."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import run_sa_04_verification_ablation_pilot as pilot

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIR = ROOT / "agent" / "verification-ablation" / "SA-04-repair-v2"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "sa_04_ccswitch_repair_round.json"
PROVIDER_PREFLIGHT = ROOT / "evaluation" / "reports" / "sa_04_ccswitch_provider_preflight.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-cost-cny", type=float, default=8.0)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--private-dir", type=Path, default=PRIVATE_DIR)
    parser.add_argument("--public-output", type=Path, default=PUBLIC_OUTPUT)
    parser.add_argument("--provider-preflight", type=Path, default=PROVIDER_PREFLIGHT)
    parser.add_argument(
        "--ccswitch-provider-id",
        default="sub2api-1789904233520",
    )
    args = parser.parse_args()
    if args.max_cost_cny <= 0 or args.max_cost_cny > 8:
        raise ValueError("repair round max cost must be between 0 and 8 CNY")
    pilot.PRIVATE_DIR = args.private_dir
    pilot.PRIVATE_ARCHIVE = args.private_dir / "pilot_archive.json"
    pilot.CHECKPOINT = args.private_dir / "pilot_checkpoint.json"
    pilot.PUBLIC_OUTPUT = args.public_output
    pilot.PROVIDER_PREFLIGHT = args.provider_preflight
    run_args = argparse.Namespace(
        approve_paid_calls=True,
        max_cost_cny=args.max_cost_cny,
        model=args.model,
        base_url=None,
        env_file=ROOT / ".env",
        resume=False,
        retry_failed=False,
        allow_prompt_revision=True,
        ccswitch_provider_id=args.ccswitch_provider_id,
    )
    result = asyncio.run(pilot.run(run_args))
    print(
        {
            "status": "completed",
            "passed": result["passed"],
            "selected_model": result["selected_model"],
            "total_reference_cost_cny": result["total_reference_cost_cny"],
            "total_reference_cost_upper_bound_cny": result[
                "total_reference_cost_upper_bound_cny"
            ],
            "public_output": str(args.public_output),
        }
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
