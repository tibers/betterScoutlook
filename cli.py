#!/usr/bin/env python3
"""
Scoutlook CLI — Command-Line Adventure Assigner & Interactive Chatbot.

Usage Examples:
  # Run interactive chatbot in demo mode:
  python3 cli.py --demo --chat

  # Assign an adventure URL to a Den in demo mode (or with --username):
  python3 cli.py --demo \
    --adventure "https://advancements.scouting.org/youthProfile/15005204/adventures/119" \
    --den "Wolf Den 3" \
    --status approved

  # Assign specific requirements to scouts by Name and BSA Member ID:
  python3 cli.py --username myleader \
    --adventure 119 \
    --requirements 1,2 \
    --scouts "Liam Carter,140882312" \
    --status completed \
    --dry-run
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import List

from chatbot import ChatbotAssistant
from scouting_client import (
    ScoutingAPIError,
    ScoutingClient,
    parse_adventure_reference,
)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scoutlook CLI — Assign Cub Scout adventures by Name, Scouting Number, or Den."
    )
    auth_grp = parser.add_argument_group("Authentication")
    auth_grp.add_argument("--username", "-u", help="My.Scouting / Scoutbook username")
    auth_grp.add_argument("--password", "-p", help="My.Scouting password (if omitted, will prompt)")
    auth_grp.add_argument("--token", "-t", help="Existing JWT Bearer token from advancements.scouting.org")
    auth_grp.add_argument("--demo", action="store_true", help="Use built-in Demo Pack 314")

    target_grp = parser.add_argument_group("Unit & Scouts")
    target_grp.add_argument("--unit-guid", help="Organization GUID (defaults to first discovered unit)")
    target_grp.add_argument("--list-roster", action="store_true", help="Print the unit roster and dens")
    target_grp.add_argument("--scouts", help="Comma-separated scout names, BSA Member IDs, or User IDs")
    target_grp.add_argument("--den", help="Comma-separated den names (e.g., 'Wolf Den 3')")

    adv_grp = parser.add_argument_group("Adventure & Status")
    adv_grp.add_argument("--list-adventures", action="store_true", help="List available Cub Scout adventures")
    adv_grp.add_argument(
        "--adventure",
        "-a",
        help="Adventure URL (e.g. https://advancements.scouting.org/youthProfile/15005204/adventures/119) or ID (e.g. 119)",
    )
    adv_grp.add_argument(
        "--mode",
        choices=["whole_adventure", "all_requirements", "requirements"],
        default="whole_adventure",
        help="Assignment scope (default: whole_adventure)",
    )
    adv_grp.add_argument(
        "--requirements",
        "-r",
        help="Comma-separated requirement numbers or IDs (e.g. '1,2')",
    )
    adv_grp.add_argument(
        "--status",
        "-s",
        choices=["started", "completed", "approved", "awarded"],
        default="approved",
        help="Advancement status to record (default: approved)",
    )
    adv_grp.add_argument("--date", help="Date in YYYY-MM-DD format (defaults to today)")
    adv_grp.add_argument("--comment", help="Optional Quick Entry comment")
    adv_grp.add_argument("--dry-run", action="store_true", help="Preview payload without submitting")
    adv_grp.add_argument("--chat", action="store_true", help="Start interactive terminal chatbot")

    args = parser.parse_args(argv)
    client = ScoutingClient()

    try:
        if args.demo:
            client.login_demo()
        elif args.token:
            client.login_with_token(args.token)
        elif args.username:
            pw = args.password or getpass.getpass(f"Password for {args.username}: ")
            client.login(args.username, pw)
        else:
            print("No credentials provided; starting in Demo Mode (--demo).", file=sys.stderr)
            client.login_demo()
    except ScoutingAPIError as exc:
        print(f"Authentication error: {exc.message}", file=sys.stderr)
        return 1

    units = client.get_units()
    if not units and not args. unit_guid:
        print("No Scouting units found for this account.", file=sys.stderr)
        return 1

    org_guid = args.unit_guid or units[0]["organization_guid"]

    if args.list_roster:
        scouts, dens = client.get_unit_roster(org_guid)
        print(f"=== Dens ({len(dens)}) ===")
        for d in dens:
            print(f"  - {d['name']} (id={d['id']})")
        print(f"\n=== Youth Roster ({len(scouts)}) ===")
        for s in scouts:
            print(
                f"  - {s.display_name:<22} | Den: {s.display_dens:<15} | "
                f"BSA ID: {s.member_id:<10} | User ID: {s.user_id}"
            )
        return 0

    if args.list_adventures:
        advs = client.get_adventures()
        print(f"=== Cub Scout Adventures ({len(advs)}) ===")
        for a in advs:
            req_str = "Required" if a.get("is_required") else "Elective"
            print(f"  #{a['id']:<4} [{a.get('rank_name', ''):<14}] {a['name']} ({req_str})")
        return 0

    if args.chat:
        bot = ChatbotAssistant(client)
        print("Scoutlook Interactive Chatbot (type 'help' for examples, 'exit' to quit)")
        state = {"org_guid": org_guid}
        while True:
            try:
                line = input("\nscoutlook> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if line.lower() in ("exit", "quit"):
                break
            res = bot.process_message(line, org_guid=org_guid, current_state=state)
            state = res.get("ui_state", state)
            print(f"\n{res['reply']}")
        return 0

    if not args.adventure:
        parser.print_help()
        return 0

    ref = parse_adventure_reference(args.adventure)
    adv_id = ref.get("adventure_id")
    if not adv_id:
        print(f"Could not parse adventure ID from: {args.adventure}", file=sys.stderr)
        return 1

    tokens: List[str] = []
    if args.scouts:
        tokens.extend([t.strip() for t in args.scouts.split(",") if t.strip()])
    dens_filter: List[str] = []
    if args.den:
        dens_filter.extend([d.strip() for d in args.den.split(",") if d.strip()])

    explicit_uids = [ref["user_id"]] if ref.get("user_id") and not tokens and not dens_filter else []
    matched_scouts, unmatched = client.resolve_scouts(
        org_guid,
        user_ids=explicit_uids,
        dens=dens_filter,
        query_tokens=tokens,
    )
    if unmatched:
        print(f"Warning: Unmatched scout/den queries: {', '.join(unmatched)}", file=sys.stderr)
    if not matched_scouts:
        print("Error: No scouts matched your criteria.", file=sys.stderr)
        return 1

    mode = args.mode
    req_ids: List[int] = []
    if args.requirements:
        mode = "requirements"
        adv_data = client.get_adventure_requirements(adv_id)
        avail_reqs = adv_data.get("requirements") or []
        wanted = [x.strip().lower() for x in args.requirements.split(",") if x.strip()]
        for w in wanted:
            for r in avail_reqs:
                if w in (
                    str(r.get("requirementNumber", "")).lower(),
                    str(r.get("listNumber", "")).lower(),
                    str(r.get("id", "")),
                ):
                    if int(r["id"]) not in req_ids:
                        req_ids.append(int(r["id"]))

    res = client.assign_adventure(
        org_guid=org_guid,
        adventure_id=adv_id,
        user_ids=[s.user_id for s in matched_scouts],
        status=args.status,
        mode=mode,
        requirement_ids=req_ids,
        completion_date=args.date,
        comment=args.comment,
        dry_run=args.dry_run,
    )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
