"""Command-line front end for the Active Work Registry -- lets a human or
an AI-assisted session (this one included) register/check/claim/complete
work items from a terminal, without the API server needing to be running
(talks to `collaboration_service` in-process, same store the API uses).

SIL Phase 2's own wording: "before any AI task begins, it must register
itself in the Active Work Registry" and "every AI worker must inspect
active work... before beginning implementation." This script is the
low-friction way to actually do both -- see CLAUDE.md's Session Protocol
section for when a session is expected to run it.

Examples:

    python tools/work_item_cli.py check --files src/futures_bot/api/app.py
    python tools/work_item_cli.py create --title "Fix login bug" --files src/auth.py --owner-type ai
    python tools/work_item_cli.py list
    python tools/work_item_cli.py claim <item_id> --user claude
    python tools/work_item_cli.py status <item_id> in_progress
    python tools/work_item_cli.py complete <item_id>
"""

from __future__ import annotations

import argparse
import json
import sys

from futures_bot.api import collaboration_service as services


def _print_json(obj) -> None:
    if hasattr(obj, "model_dump"):
        print(json.dumps(obj.model_dump(), indent=2, default=str))
    elif isinstance(obj, list):
        print(json.dumps([o.model_dump() if hasattr(o, "model_dump") else o for o in obj], indent=2, default=str))
    else:
        print(json.dumps(obj, indent=2, default=str))


def cmd_check(args: argparse.Namespace) -> int:
    result = services.pre_work_check(args.files or [], title=args.title, description=args.description)
    _print_json(result)
    if result.suggested_action == "choose_different_task":
        print("\n--> Recommendation: pick a different task or coordinate first (see above).", file=sys.stderr)
        return 1
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    item, warnings = services.create_work_item(
        title=args.title, description=args.description, owner_user_id=args.owner,
        branch=args.branch, estimated_files=args.files or [], priority=args.priority,
        owner_type=args.owner_type,
    )
    _print_json({"work_item": item.model_dump(), "overlap_warnings": [w.model_dump() for w in warnings]})
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    _print_json(services.list_work_items(status=args.status))
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    _print_json(services.claim_work_item(args.item_id, args.user))
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    _print_json(services.release_work_item(args.item_id))
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    _print_json(services.complete_work_item(args.item_id))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _print_json(services.update_work_item_status(args.item_id, args.new_status))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """SIL Phase 4's "Automatic AI Context" bundle -- everything a session
    would otherwise gather by hand before starting work, in one call."""
    result = services.get_context_bundle(
        args.files or [], title=args.title, description=args.description, commit_limit=args.commit_limit,
    )
    _print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="work_item_cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Pre-work-check: inspect active work before starting (AI awareness).")
    check.add_argument("--files", nargs="*", default=[], help="Files you plan to touch.")
    check.add_argument("--title", default=None)
    check.add_argument("--description", default=None)
    check.set_defaults(func=cmd_check)

    create = sub.add_parser("create", help="Register a new work item.")
    create.add_argument("--title", required=True)
    create.add_argument("--description", default=None)
    create.add_argument("--owner", default=None, help="User id/name claiming it immediately (optional).")
    create.add_argument("--owner-type", choices=["human", "ai"], default="human")
    create.add_argument("--branch", default=None)
    create.add_argument("--files", nargs="*", default=[])
    create.add_argument("--priority", choices=["low", "medium", "high", "critical"], default="medium")
    create.set_defaults(func=cmd_create)

    list_cmd = sub.add_parser("list", help="List work items.")
    list_cmd.add_argument("--status", default=None)
    list_cmd.set_defaults(func=cmd_list)

    claim = sub.add_parser("claim", help="Claim a work item.")
    claim.add_argument("item_id")
    claim.add_argument("--user", required=True)
    claim.set_defaults(func=cmd_claim)

    release = sub.add_parser("release", help="Release a claimed work item back to open.")
    release.add_argument("item_id")
    release.set_defaults(func=cmd_release)

    complete = sub.add_parser("complete", help="Mark a work item completed.")
    complete.add_argument("item_id")
    complete.set_defaults(func=cmd_complete)

    status = sub.add_parser("status", help="Move a work item through the lifecycle (in_progress/testing/ready_for_review/merged).")
    status.add_argument("item_id")
    status.add_argument("new_status")
    status.set_defaults(func=cmd_status)

    context = sub.add_parser("context", help="AI context bundle: active work, similar past work, recent commits, overlap, relevant docs.")
    context.add_argument("--files", nargs="*", default=[], help="Files you plan to touch.")
    context.add_argument("--title", default=None)
    context.add_argument("--description", default=None)
    context.add_argument("--commit-limit", type=int, default=10)
    context.set_defaults(func=cmd_context)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 -- a CLI tool's top level: print and exit nonzero, don't traceback
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
