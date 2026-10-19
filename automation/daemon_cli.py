"""Foreground CLI for the safe local scheduler. No shell or arbitrary-code jobs."""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
from automation.daemon import YukiDaemon, serve_until_signal

def main() -> int:
    parser = argparse.ArgumentParser(description="Yuki local approval-gated scheduler")
    parser.add_argument("--database", default="~/.yuki/jobs.sqlite")
    parser.add_argument("--notify", help="Create a scheduled local notification (no external action)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--every", type=int, help="Notification interval in seconds (minimum 5)")
    group.add_argument("--cron", help="UTC five-field cron expression, e.g. '0 9 * * 1-5'")
    args = parser.parse_args()
    if args.notify and not (args.every or args.cron):
        parser.error("--notify requires --every or --cron")
    if (args.every or args.cron) and not args.notify:
        parser.error("--every/--cron requires --notify")
    async def run():
        async def approval(name, payload):
            if name != "notify" or set(payload) != {"message"}:
                return False
            print(f"\nScheduled notification requires approval: {payload['message'][:500]}")
            try:
                answer = await asyncio.to_thread(input, "Deliver this local notification now? [y/N] ")
                return answer.strip().lower() == "y"
            except (EOFError, KeyboardInterrupt):
                return False
        daemon = YukiDaemon(Path(args.database).expanduser(), approval_required=approval)
        daemon.register_job("notify", lambda payload: print("\nYuki reminder: " + payload["message"][:500]))
        if args.notify:
            payload = {"message": args.notify[:500]}
            if args.every:
                job_id = daemon.scheduler.add_interval("notify", payload, interval_seconds=args.every)
            else:
                job_id = daemon.scheduler.add_cron("notify", payload, expression=args.cron)
            print(f"Scheduled local notification {job_id}")
        print("Yuki scheduler running. Jobs require approval when due; Ctrl-C to stop.")
        try:
            await serve_until_signal(daemon)
        except KeyboardInterrupt:
            await daemon.close()
    try:
        asyncio.run(run())
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Scheduler failed safely: {type(exc).__name__}: {exc}")
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
