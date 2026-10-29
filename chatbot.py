"""
Natural-Language Chatbot Assistant for Scoutlook.

Parses leader commands to:
- Select adventures by URL (e.g., https://advancements.scouting.org/youthProfile/15005204/adventures/119),
  numeric ID (e.g., 119), or adventure name (e.g., "Paws on the Path").
- Target scouts by Den (e.g., "Wolf Den 3", "Tiger Den 2"), Scouting Number
  (BSA Member ID or Scoutbook User ID), or Scout Name.
- Choose scope: whole adventure, all requirements, or specific requirement numbers (e.g., "reqs 1, 2").
- Choose status: started, completed, approved (leader approved), or awarded.
- Stage and preview API payloads or confirm/execute assignments immediately.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Optional, Tuple

from scouting_client import (
    ScoutMember,
    ScoutingAPIError,
    ScoutingClient,
    parse_adventure_reference,
)


class ChatbotAssistant:
    """Conversational command processor synced with ScoutingClient and the Web UI."""

    def __init__(self, client: ScoutingClient):
        self.client = client
        self.pending_action: Optional[Dict[str, Any]] = None

    def process_message(
        self,
        message: str,
        org_guid: Optional[str] = None,
        current_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a user chat message and return:
        - reply: Markdown text response
        - ui_state: Updated UI state fields to sync with the visual form
        - preview: Dry-run payload preview (if an assignment is staged)
        - executed: Result of live assignment (if confirmed/executed)
        """
        text = (message or "").strip()
        state = dict(current_state or {})
        if not text:
            return {
                "reply": "Please enter a command (for example: `Assign https://advancements.scouting.org/youthProfile/15005204/adventures/119 to Wolf Den 3 as approved`).",
                "ui_state": state,
            }

        if not self.client.token:
            return {
                "reply": "You are not signed in yet. Please sign in with your Scouting America credentials or click **Try Demo Pack** at the top right.",
                "ui_state": state,
            }

        # Ensure we have an active unit GUID
        if not org_guid:
            units = self.client.get_units()
            if units:
                org_guid = units[0]["organization_guid"]
                state["org_guid"] = org_guid
            else:
                return {
                    "reply": "No Scouting unit was found on your profile.",
                    "ui_state": state,
                }

        low = text.lower().strip()

        # 1. Check for confirmation of a pending action
        if low in ("confirm", "yes", "apply", "submit", "do it", "execute", "send"):
            if not self.pending_action:
                return {
                    "reply": "There is no pending adventure assignment to confirm. Try specifying an adventure and scouts first!",
                    "ui_state": state,
                }
            action = self.pending_action
            self.pending_action = None
            try:
                result = self.client.assign_adventure(
                    org_guid=action["org_guid"],
                    adventure_id=action["adventure_id"],
                    user_ids=action["user_ids"],
                    status=action["status"],
                    mode=action["mode"],
                    requirement_ids=action.get("requirement_ids"),
                    version_id=action.get("version_id"),
                    completion_date=action.get("completion_date"),
                    comment=action.get("comment"),
                    dry_run=False,
                )
                mode_badge = " (Demo Mode)" if result.get("demo_mode") else ""
                return {
                    "reply": (
                        f"Submitted **{result.get('adventure_name')}** (`#{result.get('adventure_id')}`) "
                        f"for **{result.get('scout_count')} scout(s)** with status **{result.get('status').upper()}**{mode_badge}.\n\n"
                        f"- **Endpoint**: `{result.get('endpoint')}`"
                    ),
                    "ui_state": state,
                    "executed": result,
                }
            except ScoutingAPIError as exc:
                return {
                    "reply": f"API error while submitting assignment: **{exc.message}**",
                    "ui_state": state,
                }

        if low in ("cancel", "nevermind", "clear", "reset"):
            self.pending_action = None
            return {
                "reply": "Cleared pending assignment.",
                "ui_state": state,
            }

        # 2. Help command
        if low in ("help", "?", "commands", "examples"):
            return {
                "reply": self._help_message(),
                "ui_state": state,
            }

        # 3. Roster / Den / Adventure informational queries
        if re.match(r"^(list|show|what are)\s+(the\s+)?dens\b", low):
            roster, dens = self.client.get_unit_roster(org_guid)
            if not dens:
                return {"reply": "No dens found for this unit.", "ui_state": state}
            lines = ["### Unit Dens"]
            for d in dens:
                count = sum(1 for s in roster if d["name"] in s.dens)
                lines.append(f"- **{d['name']}** ({count} scouts)")
            return {"reply": "\n".join(lines), "ui_state": state}

        m_who_den = re.search(
            r"(?:who\s+is\s+in|list\s+scouts\s+in|show\s+den|roster\s+for)\s+(.+?)[?.!]*$",
            text,
            re.IGNORECASE,
        )
        if m_who_den:
            query_target = m_who_den.group(1).strip()
            matched, _ = self.client.resolve_scouts(
                org_guid, query_tokens=[query_target]
            )
            if not matched:
                return {
                    "reply": f"No scouts matched `{query_target}`.",
                    "ui_state": state,
                }
            state["user_ids"] = [s.user_id for s in matched]
            lines = [f"### Scouts matching `{query_target}` ({len(matched)})"]
            for s in matched:
                lines.append(
                    f"- **{s.display_name}** — Den: `{s.display_dens}` | BSA ID: `{s.member_id}` | User ID: `{s.user_id}`"
                )
            lines.append("\n*(Selected these scouts in the visual roster.)*")
            return {"reply": "\n".join(lines), "ui_state": state}

        if re.match(r"^(list|show)\s+(all\s+)?scouts|roster$", low):
            roster, _ = self.client.get_unit_roster(org_guid)
            lines = [f"### Unit Roster ({len(roster)} Scouts)"]
            for s in roster[:25]:
                lines.append(
                    f"- **{s.display_name}** — `{s.display_dens}` (BSA ID: `{s.member_id}`, User ID: `{s.user_id}`)"
                )
            if len(roster) > 25:
                lines.append(f"- *...and {len(roster) - 25} more scouts.*")
            return {"reply": "\n".join(lines), "ui_state": state}

        if re.match(r"^(list|show)\s+(all\s+)?(.*\s+)?adventures\b", low):
            advs = self.client.get_adventures()
            rank_filter = None
            for rname in ("lion", "tiger", "wolf", "bear", "webelos", "arrow of light", "bobcat"):
                if rname in low:
                    rank_filter = rname
                    break
            filtered = [
                a
                for a in advs
                if not rank_filter or rank_filter in a.get("rank_name", "").lower()
            ]
            lines = [
                f"### Available Adventures ({len(filtered)})"
            ]
            for a in filtered[:25]:
                req_tag = "Required" if a.get("is_required") else "Elective"
                lines.append(
                    f"- **`#{a['id']}` {a['name']}** ({a.get('rank_name')} — {req_tag})"
                )
            if len(filtered) > 25:
                lines.append(f"- *...and {len(filtered) - 25} more.*")
            return {"reply": "\n".join(lines), "ui_state": state}

        # 4. Parse Adventure Selection + Scout Assignment intent
        parsed = self._parse_assignment_command(text, org_guid, state)
        if not parsed["adventure"] and not parsed["matched_scouts"]:
            return {
                "reply": (
                    "I couldn't identify an adventure or scouts in that message.\n\n"
                    + self._help_message()
                ),
                "ui_state": state,
            }

        # Update UI state with everything resolved so far
        if parsed["adventure"]:
            adv = parsed["adventure"]
            state["adventure_id"] = adv["id"]
            state["version_id"] = parsed["version_id"] or adv.get("active_version_id")
        if parsed["mode"]:
            state["mode"] = parsed["mode"]
        if parsed["requirement_ids"] is not None:
            state["requirement_ids"] = parsed["requirement_ids"]
        if parsed["status"]:
            state["status"] = parsed["status"]
        if parsed["completion_date"]:
            state["completion_date"] = parsed["completion_date"]
        if parsed["matched_scouts"]:
            state["user_ids"] = [s.user_id for s in parsed["matched_scouts"]]

        # If only an adventure was selected (or URL pasted) without extra target scouts
        adv_obj = parsed["adventure"]
        scouts_list: List[ScoutMember] = parsed["matched_scouts"]
        unmatched_tokens: List[str] = parsed["unmatched"]

        if adv_obj and not scouts_list:
            # Check if UI state already had selected scouts
            existing_uids = state.get("user_ids") or []
            if existing_uids:
                scouts_list, _ = self.client.resolve_scouts(
                    org_guid, user_ids=existing_uids
                )

        if not adv_obj:
            # Maybe an adventure was already selected in UI state
            existing_adv_id = state.get("adventure_id")
            if existing_adv_id:
                adv_data = self.client.get_adventure_requirements(
                    int(existing_adv_id), state.get("version_id")
                )
                adv_obj = adv_data["adventure"]
                parsed["version_id"] = adv_data["version_id"]

        if adv_obj and not scouts_list:
            msg_parts = [
                f"Selected adventure **`#{adv_obj['id']}` {adv_obj['name']}** ({adv_obj.get('rank_name', 'Cub Scout')})."
            ]
            if unmatched_tokens:
                msg_parts.append(
                    f"\n> Could not match scout/den query: `{', '.join(unmatched_tokens)}`"
                )
            msg_parts.append(
                "\nWhich scouts or den should receive this adventure? "
                "(e.g., `assign to Wolf Den 3`, `assign to 15005204`, or `assign to Liam Carter`)"
            )
            return {"reply": "\n".join(msg_parts), "ui_state": state}

        if scouts_list and not adv_obj:
            names_preview = ", ".join(
                f"**{s.display_name}** (`{s.user_id}`)" for s in scouts_list[:8]
            )
            if len(scouts_list) > 8:
                names_preview += f", +{len(scouts_list) - 8} more"
            return {
                "reply": (
                    f"Selected **{len(scouts_list)} scout(s)**: {names_preview}.\n\n"
                    "Now specify an adventure URL, ID, or name (e.g., `assign adventure 119 as approved`)."
                ),
                "ui_state": state,
            }

        # Both adventure and scouts are resolved -> create dry_run preview (or execute if requested)
        eff_mode = state.get("mode") or "whole_adventure"
        eff_status = state.get("status") or "approved"
        eff_date = state.get("completion_date") or dt.date.today().strftime("%Y-%m-%d")
        eff_reqs = state.get("requirement_ids") or []
        eff_vid = parsed["version_id"] or state.get("version_id") or adv_obj.get("active_version_id")
        target_uids = [s.user_id for s in scouts_list]

        preview = self.client.assign_adventure(
            org_guid=org_guid,
            adventure_id=int(adv_obj["id"]),
            user_ids=target_uids,
            status=eff_status,
            mode=eff_mode,
            requirement_ids=eff_reqs,
            version_id=eff_vid,
            completion_date=eff_date,
            dry_run=True,
        )

        self.pending_action = {
            "org_guid": org_guid,
            "adventure_id": int(adv_obj["id"]),
            "user_ids": target_uids,
            "status": eff_status,
            "mode": eff_mode,
            "requirement_ids": eff_reqs,
            "version_id": eff_vid,
            "completion_date": eff_date,
            "comment": None,
        }

        scout_bullets = "\n".join(
            f"  - **{s.display_name}** (Den: `{s.display_dens}`, BSA ID: `{s.member_id}`, User ID: `{s.user_id}`)"
            for s in scouts_list[:12]
        )
        if len(scouts_list) > 12:
            scout_bullets += f"\n  - *...and {len(scouts_list) - 12} more*"

        scope_desc = (
            "Entire Adventure"
            if eff_mode == "whole_adventure"
            else "All Requirements"
            if eff_mode == "all_requirements"
            else f"{len(eff_reqs)} Selected Requirement(s) ({', '.join( parsed['requirement_labels'] or [str(r) for r in eff_reqs] )})"
        )

        unmatched_note = ""
        if unmatched_tokens:
            unmatched_note = f"\n- **Unmatched tokens**: `{', '.join(unmatched_tokens)}`"

        reply = (
            f"### Staged Adventure Assignment\n"
            f"- **Adventure**: `{adv_obj['id']}` — **{adv_obj['name']}** ({adv_obj.get('rank_name', 'Cub Scout')})\n"
            f"- **Scope**: {scope_desc}\n"
            f"- **Status**: `{eff_status.upper()}` (Date: `{eff_date}`)\n"
            f"- **Target Scouts ({len(scouts_list)})**:\n{scout_bullets}"
            f"{unmatched_note}\n\n"
            f"Reply **`confirm`** (or click **Assign Adventure** in the form) to submit this to Scoutbook Plus."
        )

        return {
            "reply": reply,
            "ui_state": state,
            "preview": preview,
        }

    def _parse_assignment_command(
        self, text: str, org_guid: str, current_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract adventure, requirements, status, date, and target scouts from free text."""
        working = text.strip()
        advs = self.client.get_adventures()

        resolved_adv: Optional[Dict[str, Any]] = None
        url_user_id: Optional[int] = None

        # 1. Check for URL e.g. https://advancements.scouting.org/youthProfile/15005204/adventures/119
        m_url = re.search(
            r"https?://[^\s]+/adventures/\d+|/youthProfile/\d+/adventures/\d+|/adventures/\d+",
            working,
            re.IGNORECASE,
        )
        if m_url:
            ref_info = parse_adventure_reference(m_url.group(0))
            if ref_info["adventure_id"]:
                adv_id = ref_info["adventure_id"]
                resolved_adv = next((a for a in advs if a["id"] == adv_id), None)
                if not resolved_adv:
                    adv_data = self.client.get_adventure_requirements(adv_id)
                    resolved_adv = adv_data["adventure"]
            if ref_info["user_id"]:
                url_user_id = ref_info["user_id"]
            working = working.replace(m_url.group(0), " ")

        # 2. Check for "adventure #119" or "adventure 119"
        if not resolved_adv:
            m_adv_num = re.search(r"\badventure\s*#?\s*(\d+)\b", working, re.IGNORECASE)
            if m_adv_num:
                adv_id = int(m_adv_num.group(1))
                resolved_adv = next((a for a in advs if a["id"] == adv_id), None)
                if not resolved_adv:
                    adv_data = self.client.get_adventure_requirements(adv_id)
                    resolved_adv = adv_data["adventure"]
                working = (
                    working[: m_adv_num.start()] + " " + working[m_adv_num.end() :]
                )

        # 3. Check for Adventure Name match (longest name first)
        if not resolved_adv:
            low_work = working.lower()
            for adv in sorted(advs, key=lambda a: len(a["name"]), reverse=True):
                aname = adv["name"].lower()
                if aname and re.search(rf"\b{re.escape(aname)}\b", low_work):
                    resolved_adv = adv
                    working = re.sub(
                        rf"\b{re.escape(aname)}\b", " ", working, flags=re.IGNORECASE
                    )
                    break

        # 4. Extract Date (YYYY-MM-DD)
        completion_date: Optional[str] = None
        m_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", working)
        if m_date:
            completion_date = m_date.group(1)
            working = working.replace(m_date.group(0), " ")

        # 5. Extract Status
        status: Optional[str] = None
        low_work = working.lower()
        if re.search(r"\b(awarded|award)\b", low_work):
            status = "awarded"
        elif re.search(r"\b(leader\s+approved|approved|approve)\b", low_work):
            status = "approved"
        elif re.search(r"\b(completed|complete)\b", low_work):
            status = "completed"
        elif re.search(r"\b(started|start)\b", low_work):
            status = "started"

        # 6. Extract Requirements / Scope
        mode: Optional[str] = None
        requirement_ids: Optional[List[int]] = None
        requirement_labels: List[str] = []
        version_id: Optional[int] = None

        active_adv = resolved_adv
        if not active_adv and current_state.get("adventure_id"):
            active_adv = next(
                (a for a in advs if a["id"] == int(current_state["adventure_id"])),
                None,
            )

        if re.search(r"\ball\s+requirements?\b", working, re.IGNORECASE):
            mode = "all_requirements"
            working = re.sub(
                r"\ball\s+requirements?\b", " ", working, flags=re.IGNORECASE
            )
        else:
            m_reqs = re.search(
                r"\b(?:requirements?|reqs?)\s+([0-9a-zA-Z,\s]+?)(?=\s+(?:to|for|as|on|in)\b|$)",
                working,
                re.IGNORECASE,
            )
            if m_reqs and active_adv:
                raw_req_tokens = [
                    t.strip().lower()
                    for t in re.split(r"[,\s]+", m_reqs.group(1))
                    if t.strip() and t.strip().lower() != "and"
                ]
                adv_req_data = self.client.get_adventure_requirements(active_adv["id"])
                version_id = adv_req_data.get("version_id")
                avail_reqs = adv_req_data.get("requirements") or []
                matched_rids: List[int] = []
                for tok in raw_req_tokens:
                    for r in avail_reqs:
                        rnum = str(r.get("requirementNumber") or "").lower()
                        lnum = str(r.get("listNumber") or "").lower()
                        rid_str = str(r.get("id") or "")
                        if tok in (rnum, lnum, rid_str):
                            if int(r["id"]) not in matched_rids:
                                matched_rids.append(int(r["id"]))
                                requirement_labels.append(
                                    str(r.get("requirementNumber") or r["id"])
                                )
                if matched_rids:
                    mode = "requirements"
                    requirement_ids = matched_rids
                working = (
                    working[: m_reqs.start()] + " " + working[m_reqs.end() :]
                )

        # 7. Extract Target Scouts (by "to <targets>" or "for <targets>" or remaining tokens)
        target_segment = ""
        m_targets = re.search(
            r"\b(?:to|for)\s+(.+?)(?=\s+as\s+(?:started|completed|approved|leader\s+approved|awarded)\b|\s+on\s+20\d{2}|$)",
            working,
            re.IGNORECASE,
        )
        if m_targets:
            target_segment = m_targets.group(1).strip()
        else:
            # Strip common command verbs and status phrases to see if scout/den names remain
            cleaned = re.sub(
                r"\b(assign|select|mark|record|set|give|start|complete|approve|award|whole\s+adventure|entire\s+adventure|as\s+started|as\s+completed|as\s+approved|as\s+leader\s+approved|as\s+awarded|started|completed|approved|awarded|please)\b",
                " ",
                working,
                flags=re.IGNORECASE,
            )
            target_segment = cleaned.strip()

        raw_tokens = [
            t.strip()
            for t in re.split(r",|\band\b|;", target_segment, flags=re.IGNORECASE)
            if t.strip()
        ]
        # Clean leading prepositions like "den Wolf Den 3" or "scout Liam Carter"
        cleaned_tokens: List[str] = []
        for tok in raw_tokens:
            t2 = re.sub(r"^(?:scouts?|youth|member|user|id)\s+", "", tok, flags=re.IGNORECASE).strip()
            t2 = t2.strip(".!? ")
            if t2:
                cleaned_tokens.append(t2)

        matched_scouts, unmatched = self.client.resolve_scouts(
            org_guid,
            user_ids=[url_user_id] if url_user_id and not cleaned_tokens else None,
            query_tokens=cleaned_tokens,
        )

        # If URL had a userId AND the user also mentioned other targets, only include url_user_id if explicitly desired or if no other targets were specified
        return {
            "adventure": resolved_adv,
            "version_id": version_id
            or (resolved_adv.get("active_version_id") if resolved_adv else None),
            "mode": mode,
            "requirement_ids": requirement_ids,
            "requirement_labels": requirement_labels,
            "status": status,
            "completion_date": completion_date,
            "matched_scouts": matched_scouts,
            "unmatched": unmatched,
        }

    @staticmethod
    def _help_message() -> str:
        return (
            "### Scoutlook Assistant Examples\n"
            "- **Assign by URL & Den**:\n"
            "  `Assign https://advancements.scouting.org/youthProfile/15005204/adventures/119 to Wolf Den 3 as approved`\n"
            "- **Assign by Adventure Name & Scout Names / IDs**:\n"
            "  `Assign Paws on the Path to Liam Carter, 140882312, 15005206 as completed`\n"
            "- **Assign Specific Requirements**:\n"
            "  `Mark Paws on the Path requirements 1, 2 for Wolf Den 3 as approved`\n"
            "- **Query Roster / Dens**:\n"
            "  `list dens`, `who is in Wolf Den 3?`, or `list Wolf adventures`\n"
            "- **Submit Staged Action**:\n"
            "  `confirm`"
        )
