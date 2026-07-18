from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JUSHUITAN_ROOT = PROJECT_ROOT.parent / "jushuitan-sku-offline-batch"
DEFAULT_SHARED_RUNTIME_ROOT = Path(os.environ.get("SCRIPT_1688_ROOT", "D:/script_1688"))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="1688 SKU offline + Jushuitan link cleanup pipeline")
    parser.add_argument("--file", required=True, help="CSV/XLSX stop-sale input file")
    parser.add_argument("--mode", choices=("preview", "execute"), default="preview")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--yes", action="store_true", help="Required for live execute mode")
    parser.add_argument("--skip-login", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--jushuitan-root", default=str(DEFAULT_JUSHUITAN_ROOT))
    parser.add_argument("--shared-runtime-root", default=str(DEFAULT_SHARED_RUNTIME_ROOT))
    parser.add_argument("--shared-lock-path", default="")
    parser.add_argument("--lock-wait-seconds", type=int, default=7200)
    parser.add_argument("--lock-stale-seconds", type=int, default=21600)
    parser.add_argument("--lock-poll-seconds", type=float, default=10.0)
    parser.add_argument(
        "--no-shared-lock",
        action="store_true",
        help="Disable the shared D:/script_1688 lock. Only safe on an isolated machine.",
    )
    return parser


def build_1688_command(args: argparse.Namespace, handoff_path: Path) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "rpa" / "sku_offline_main.py"),
        "--mode",
        args.mode,
        "--file",
        str(Path(args.file).resolve()),
        "--jushuitan-handoff-out",
        str(handoff_path),
    ]
    if args.limit > 0:
        command.extend(["--limit", str(args.limit)])
    if args.mode == "execute":
        command.append("--yes")
    if args.skip_login:
        command.append("--skip-login")
    if args.no_notify:
        command.append("--no-notify")
    return command


def build_jushuitan_command(
    args: argparse.Namespace,
    handoff_path: Path,
    jushuitan_root: Path,
) -> list[str]:
    command = [
        "npm.cmd" if sys.platform == "win32" else "npm",
        "run",
        "cleanup:1688",
        "--",
        "--mode",
        args.mode,
        "--file",
        str(handoff_path),
    ]
    if args.mode == "execute":
        command.append("--yes")
    if args.no_notify:
        command.append("--no-notify")
    return command


def count_handoff_records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def send_pipeline_notification(content: str, *, disabled: bool) -> bool:
    if disabled:
        return False
    rpa_root = PROJECT_ROOT / "rpa"
    if str(rpa_root) not in sys.path:
        sys.path.insert(0, str(rpa_root))
    from dingtalk import post_dingtalk_text_message

    return post_dingtalk_text_message(
        {
            "notifications": {
                "dingtalk": {
                    "enabled": True,
                    "webhook_env": "DINGTALK_WEBHOOK",
                    "secret_env": "DINGTALK_SECRET",
                }
            }
        },
        content,
    )


def resolve_shared_lock_path(args: argparse.Namespace) -> Path:
    configured = str(args.shared_lock_path or "").strip()
    if configured:
        return Path(configured).resolve()
    return Path(args.shared_runtime_root).resolve() / "artifacts" / "locks" / "ali1688_full_cycle.lock"


def build_shared_lock(args: argparse.Namespace, run_id: str):
    if args.mode != "execute" or args.no_shared_lock:
        return nullcontext(), None, ""

    runtime_root = Path(args.shared_runtime_root).resolve()
    lock_module = runtime_root / "src" / "runtime" / "global_lock.py"
    if not lock_module.exists():
        raise FileNotFoundError(f"Shared 1688 runtime lock module not found: {lock_module}")
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

    from src.runtime.global_lock import GlobalFileLock, GlobalLockTimeoutError

    lock_path = resolve_shared_lock_path(args)
    lock = GlobalFileLock(
        lock_path,
        stale_after_seconds=args.lock_stale_seconds,
        wait_timeout_seconds=args.lock_wait_seconds,
        poll_interval_seconds=args.lock_poll_seconds,
        metadata={
            "cycle": "1688_stop_sale_pipeline",
            "run_id": run_id,
            "source": str(Path(args.file).resolve()),
        },
    )
    return lock, GlobalLockTimeoutError, str(lock_path)


def run_pipeline(
    args: argparse.Namespace,
    *,
    run_id: str,
    pipeline_dir: Path,
    jushuitan_root: Path,
    shared_lock_path: str,
) -> int:
    handoff_path = pipeline_dir / f"{run_id}.jushuitan.jsonl"
    summary_path = pipeline_dir / f"{run_id}.summary.json"

    started_at = datetime.now().isoformat(timespec="seconds")
    command_1688 = build_1688_command(args, handoff_path)
    result_1688 = subprocess.run(command_1688, cwd=PROJECT_ROOT, check=False)
    handoff_count = count_handoff_records(handoff_path)

    jushuitan_return_code: int | None = None
    if result_1688.returncode == 0 and handoff_count > 0:
        command_jushuitan = build_jushuitan_command(args, handoff_path, jushuitan_root)
        result_jushuitan = subprocess.run(command_jushuitan, cwd=jushuitan_root, check=False)
        jushuitan_return_code = result_jushuitan.returncode

    summary = {
        "run_id": run_id,
        "mode": args.mode,
        "input_file": str(Path(args.file).resolve()),
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "1688_return_code": result_1688.returncode,
        "jushuitan_return_code": jushuitan_return_code,
        "jushuitan_handoff_path": str(handoff_path),
        "jushuitan_handoff_count": handoff_count,
        "shared_lock_path": shared_lock_path,
        "summary_path": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if result_1688.returncode != 0:
        return result_1688.returncode
    if handoff_count == 0:
        return 0
    return int(jushuitan_return_code or 0)


def main() -> int:
    args = build_argument_parser().parse_args()
    if args.limit < 0:
        raise ValueError("--limit must be non-negative")
    if args.lock_wait_seconds < 0 or args.lock_stale_seconds <= 0 or args.lock_poll_seconds <= 0:
        raise ValueError("Shared lock timing values must be positive (wait may be zero)")
    if args.mode == "execute" and not args.yes:
        raise ValueError("execute mode requires --yes")

    jushuitan_root = Path(args.jushuitan_root).resolve()
    if not (jushuitan_root / "package.json").exists():
        raise FileNotFoundError(f"Jushuitan project not found: {jushuitan_root}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    pipeline_dir = PROJECT_ROOT / "logs" / "sku_offline" / "pipelines"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    lock, timeout_error, lock_path = build_shared_lock(args, run_id)
    try:
        with lock:
            return run_pipeline(
                args,
                run_id=run_id,
                pipeline_dir=pipeline_dir,
                jushuitan_root=jushuitan_root,
                shared_lock_path=lock_path,
            )
    except Exception as exc:
        if timeout_error is not None and isinstance(exc, timeout_error):
            print(f"Shared 1688 runtime lock timed out: {lock_path}")
            send_pipeline_notification(
                "【1688 停产下架】\n"
                f"批次：{run_id}\n"
                "状态：未启动\n"
                "原因：其他 1688 任务仍在运行，共享浏览器资源占用\n"
                "处理：调度器稍后重试\n"
                "退出码：75",
                disabled=args.no_notify,
            )
            return 75
        raise


if __name__ == "__main__":
    raise SystemExit(main())
