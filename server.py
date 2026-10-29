"""
Scoutlook Web Server & REST API.

Provides:
- Web UI at `/` (`static/index.html`)
- Authentication endpoints (`/api/login`, `/api/logout`, `/api/session`)
- Unit & Roster discovery (`/api/units`, `/api/units/{org_guid}/roster`)
- Adventure & Requirements lookup (`/api/adventures`, `/api/adventures/{adventure_id}/requirements`, `/api/parse-url`)
- Scout resolution by Name, Scouting Number, or Den (`/api/resolve-scouts`)
- Adventure & Requirement assignment (`/api/assign`)
- Natural-language Chatbot Assistant (`/api/chat`)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chatbot import ChatbotAssistant
from scouting_client import (
    ScoutingAPIError,
    ScoutingClient,
    parse_adventure_reference,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="Scoutlook — Scoutbook Plus Adventure Assigner",
    description="Select Cub Scout adventures by URL or Rank and assign them to scouts by Name, Scouting Number, or Den.",
    version="1.0.0",
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Single-user / local leader session state
client = ScoutingClient()
chatbot = ChatbotAssistant(client)


# -----------------------------------------------------------------------------
# Request Models
# -----------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    demo: bool = False


class ParseUrlRequest(BaseModel):
    reference: str
    org_guid: Optional[str] = None


class ResolveScoutsRequest(BaseModel):
    org_guid: str
    user_ids: List[int] = Field(default_factory=list)
    scouting_numbers: List[str] = Field(default_factory=list)
    names: List[str] = Field(default_factory=list)
    dens: List[str] = Field(default_factory=list)
    query_tokens: List[str] = Field(default_factory=list)


class AssignAdventureRequest(BaseModel):
    org_guid: str
    adventure_id: int
    user_ids: List[int]
    status: str = "approved"  # "started", "completed", "approved", "awarded"
    mode: str = "whole_adventure"  # "whole_adventure", "all_requirements", "requirements"
    requirement_ids: List[int] = Field(default_factory=list)
    version_id: Optional[int] = None
    completion_date: Optional[str] = None
    comment: Optional[str] = None
    dry_run: bool = False


class ChatRequest(BaseModel):
    message: str
    org_guid: Optional[str] = None
    current_state: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Helper for ScoutingAPIError handling
# -----------------------------------------------------------------------------


def _handle_api_error(exc: ScoutingAPIError) -> JSONResponse:
    status = exc.status_code if 400 <= exc.status_code < 600 else 502
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "error": exc.message,
            "details": exc.details,
        },
    )


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="static/index.html not found")
    return FileResponse(str(index_path))


@app.get("/api/session")
def get_session() -> Dict[str, Any]:
    if not client.token:
        return {"authenticated": False, "demo_mode": False, "units": []}
    return {
        "authenticated": True,
        "demo_mode": client.demo_mode,
        "person_guid": client.person_guid,
        "user_id": client.user_id,
        "member_id": client.member_id,
        "first_name": client.first_name,
        "last_name": client.last_name,
        "units": client.get_units(),
    }


@app.post("/api/login")
def login(req: LoginRequest):
    try:
        if req.demo:
            info = client.login_demo()
            chatbot.pending_action = None
            return {"ok": True, **info}
        if req.token and req.token.strip():
            info = client.login_with_token(req.token.strip())
            chatbot.pending_action = None
            return {"ok": True, **info}
        if req.username and req.password:
            info = client.login(req.username.strip(), req.password)
            chatbot.pending_action = None
            return {"ok": True, **info}
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "Please provide a username and password, a Bearer token, or select Demo Mode.",
            },
        )
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


@app.post("/api/logout")
def logout() -> Dict[str, Any]:
    global client, chatbot
    client = ScoutingClient()
    chatbot = ChatbotAssistant(client)
    return {"ok": True, "authenticated": False}


@app.get("/api/units")
def list_units(refresh: bool = Query(False)):
    if not client.token:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "Not authenticated."}
        )
    try:
        units = client.get_units(force_refresh=refresh)
        return {"ok": True, "units": units}
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


@app.get("/api/units/{org_guid}/roster")
def get_roster(org_guid: str, refresh: bool = Query(False)):
    if not client.token:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "Not authenticated."}
        )
    try:
        scouts, dens = client.get_unit_roster(org_guid, force_refresh=refresh)
        return {
            "ok": True,
            "org_guid": org_guid,
            "scouts": [s.to_dict() for s in scouts],
            "dens": dens,
        }
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


@app.get("/api/adventures")
def list_adventures(refresh: bool = Query(False)):
    if not client.token:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "Not authenticated."}
        )
    try:
        ranks = client.get_ranks()
        adventures = client.get_adventures(force_refresh=refresh)
        return {"ok": True, "ranks": ranks, "adventures": adventures}
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


@app.get("/api/adventures/{adventure_id}/requirements")
def get_adventure_requirements(
    adventure_id: int, version_id: Optional[int] = Query(None)
):
    if not client.token:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "Not authenticated."}
        )
    try:
        data = client.get_adventure_requirements(adventure_id, version_id=version_id)
        return {"ok": True, **data}
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


@app.post("/api/parse-url")
def parse_url_endpoint(req: ParseUrlRequest):
    parsed = parse_adventure_reference(req.reference)
    adv_id = parsed.get("adventure_id")
    uid = parsed.get("user_id")
    if not adv_id:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": (
                    "Could not extract an adventure ID from that input. "
                    "Expected a URL like https://advancements.scouting.org/youthProfile/15005204/adventures/119 or a numeric ID like 119."
                ),
            },
        )

    result: Dict[str, Any] = {
        "ok": True,
        "adventure_id": adv_id,
        "user_id": uid,
    }
    if client.token:
        try:
            adv_data = client.get_adventure_requirements(adv_id)
            result.update(adv_data)
            if uid and req.org_guid:
                matched, _ = client.resolve_scouts(req.org_guid, user_ids=[uid])
                if matched:
                    result["scout"] = matched[0].to_dict()
        except ScoutingAPIError as exc:
            result["warning"] = exc.message

    return result


@app.post("/api/resolve-scouts")
def resolve_scouts_endpoint(req: ResolveScoutsRequest):
    if not client.token:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "Not authenticated."}
        )
    try:
        matched, unmatched = client.resolve_scouts(
            req.org_guid,
            user_ids=req.user_ids,
            scouting_numbers=req.scouting_numbers,
            names=req.names,
            dens=req.dens,
            query_tokens=req.query_tokens,
        )
        return {
            "ok": True,
            "matched": [s.to_dict() for s in matched],
            "unmatched": unmatched,
        }
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


@app.post("/api/assign")
def assign_adventure_endpoint(req: AssignAdventureRequest):
    if not client.token:
        return JSONResponse(
            status_code=401, content={"ok": False, "error": "Not authenticated."}
        )
    if not req.user_ids:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Please select at least one scout."},
        )
    try:
        res = client.assign_adventure(
            org_guid=req.org_guid,
            adventure_id=req.adventure_id,
            user_ids=req.user_ids,
            status=req.status,
            mode=req.mode,
            requirement_ids=req.requirement_ids,
            version_id=req.version_id,
            completion_date=req.completion_date,
            comment=req.comment,
            dry_run=req.dry_run,
        )
        return res
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    try:
        res = chatbot.process_message(
            req.message,
            org_guid=req.org_guid,
            current_state=req.current_state,
        )
        return {"ok": True, **res}
    except ScoutingAPIError as exc:
        return _handle_api_error(exc)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=False)
