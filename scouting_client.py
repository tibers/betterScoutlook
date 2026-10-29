"""
Scoutbook Plus / Internet Advancement (advancements.scouting.org) API Client.

Communicates with:
- https://auth.scouting.org (Authentication & Session Refresh)
- https://api.scouting.org  (Persons, Organizations/Units, SubUnits/Dens, Advancements)
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

AUTH_BASE_URL = "https://auth.scouting.org"
API_BASE_URL = "https://api.scouting.org"
WEB_ORIGIN = "https://advancements.scouting.org"

# Cub Scout Rank IDs commonly used in api.scouting.org/advancements/v2/ranks
CUB_RANK_NAMES = {
    1: "Bobcat",
    2: "Tiger",
    3: "Wolf",
    4: "Bear",
    5: "Webelos",
    6: "Arrow of Light",
    7: "Lion",
}


class ScoutingAPIError(Exception):
    """Raised when auth.scouting.org or api.scouting.org returns an error."""

    def __init__(self, message: str, status_code: int = 0, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    """Decode the payload section of a JWT without external dependencies."""
    raw = token.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    padding = "=" * ((4 - len(payload_b64) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def parse_adventure_reference(ref: str) -> Dict[str, Optional[int]]:
    """
    Parse an adventure URL or ID string.

    Examples supported:
    - https://advancements.scouting.org/youthProfile/15005204/adventures/119
      -> {"adventure_id": 119, "user_id": 15005204}
    - https://advancements.scouting.org/adventures/119
      -> {"adventure_id": 119, "user_id": None}
    - "119"
      -> {"adventure_id": 119, "user_id": None}
    """
    text = (ref or "").strip()
    result: Dict[str, Optional[int]] = {"adventure_id": None, "user_id": None}
    if not text:
        return result

    # Match youthProfile/{userId}/adventures/{adventureId}
    m_youth_adv = re.search(r"/youthProfile/(\d+)/adventures/(\d+)", text, re.IGNORECASE)
    if m_youth_adv:
        result["user_id"] = int(m_youth_adv.group(1))
        result["adventure_id"] = int(m_youth_adv.group(2))
        return result

    # Match /adventures/{adventureId}
    m_adv = re.search(r"/adventures/(\d+)", text, re.IGNORECASE)
    if m_adv:
        result["adventure_id"] = int(m_adv.group(1))
        return result

    # Match plain integer ID
    if re.fullmatch(r"\d+", text):
        result["adventure_id"] = int(text)
        return result

    return result


@dataclass
class ScoutMember:
    user_id: int
    member_id: str
    person_guid: str
    first_name: str
    last_name: str
    middle_name: str = ""
    nick_name: str = ""
    age: Optional[int] = None
    current_rank: str = ""
    dens: List[str] = field(default_factory=list)
    den_ids: List[int] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self) -> str:
        if self.nick_name and self.nick_name.lower() != self.first_name.lower():
            return f"{self.first_name} ({self.nick_name}) {self.last_name}".strip()
        return self.full_name

    @property
    def display_dens(self) -> str:
        return ", ".join(self.dens) if self.dens else "Unassigned"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["full_name"] = self.full_name
        d["display_name"] = self.display_name
        d["display_dens"] = self.display_dens
        return d


class ScoutingClient:
    """Client for Scouting America / Scoutbook Plus APIs with optional Demo mode."""

    def __init__(self, timeout: float = 25.0):
        self.timeout = timeout
        self.token: Optional[str] = None
        self.person_guid: Optional[str] = None
        self.user_id: Optional[int] = None
        self.member_id: Optional[str] = None
        self.username: Optional[str] = None
        self.first_name: Optional[str] = None
        self.last_name: Optional[str] = None
        self.demo_mode: bool = False

        # Caches
        self.units_cache: List[Dict[str, Any]] = []
        self.roster_cache: Dict[str, List[ScoutMember]] = {}
        self.dens_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.ranks_cache: List[Dict[str, Any]] = []
        self.adventures_cache: List[Dict[str, Any]] = []
        self.requirements_cache: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        accept_version: Optional[str] = None,
    ) -> Any:
        if params:
            query_items: List[Tuple[str, str]] = []
            for k, v in params.items():
                if v is None:
                    continue
                if isinstance(v, (list, tuple, set)):
                    for item in v:
                        query_items.append((k, str(item)))
                else:
                    query_items.append((k, str(v)))
            if query_items:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}{urllib.parse.urlencode(query_items)}"

        req_headers = {
            "Accept": f"application/json; version = {accept_version}"
            if accept_version
            else "application/json",
            "Content-Type": "application/json",
            "Origin": WEB_ORIGIN,
            "Referer": f"{WEB_ORIGIN}/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        }
        if self.token:
            req_headers["Authorization"] = f"bearer {self.token}"
        if headers:
            req_headers.update(headers)

        data_bytes = None
        if body is not None:
            data_bytes = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            url, data=data_bytes, headers=req_headers, method=method.upper()
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {"raw": raw}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            parsed_err: Any = err_body
            err_msg = f"HTTP {exc.code}: {exc.reason}"
            try:
                parsed_err = json.loads(err_body)
                if isinstance(parsed_err, dict):
                    err_msg = (
                        parsed_err.get("message")
                        or parsed_err.get("errorDesc")
                        or parsed_err.get("error")
                        or parsed_err.get("ResponseDescription")
                        or err_msg
                    )
            except Exception:
                pass
            raise ScoutingAPIError(
                message=str(err_msg), status_code=exc.code, details=parsed_err
            ) from exc
        except urllib.error.URLError as exc:
            raise ScoutingAPIError(
                message=f"Network error connecting to Scouting API: {exc.reason}",
                status_code=0,
            ) from exc

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate against https://auth.scouting.org/api/users/{username}/authenticate."""
        self.demo_mode = False
        clean_user = urllib.parse.quote(username.strip(), safe="")
        url = f"{AUTH_BASE_URL}/api/users/{clean_user}/authenticate"
        resp = self._request(
            "POST",
            url,
            body={"password": password},
            accept_version="2",
        )
        if not isinstance(resp, dict):
            raise ScoutingAPIError("Unexpected authentication response format.")

        token = (
            resp.get("token")
            or resp.get("access_token")
            or (resp.get("account") or {}).get("token")
        )
        if not token:
            msg = (
                resp.get("message")
                or resp.get("error")
                or "Authentication failed: no token returned."
            )
            raise ScoutingAPIError(str(msg), status_code=401, details=resp)

        self.username = username.strip()
        return self._initialize_from_auth_payload(token, resp)

    def login_with_token(self, token: str) -> Dict[str, Any]:
        """Authenticate using an existing JWT Bearer token from advancements.scouting.org."""
        self.demo_mode = False
        raw_token = token.strip()
        if raw_token.lower().startswith("bearer "):
            raw_token = raw_token[7:].strip()
        if not raw_token:
            raise ScoutingAPIError("Token cannot be empty.", status_code=400)
        return self._initialize_from_auth_payload(raw_token, {})

    def _initialize_from_auth_payload(
        self, token: str, auth_resp: Dict[str, Any]
    ) -> Dict[str, Any]:
        self.token = token
        claims = decode_jwt_payload(token)
        account = auth_resp.get("account") if isinstance(auth_resp.get("account"), dict) else {}

        self.person_guid = (
            auth_resp.get("personGuid")
            or account.get("personGuid")
            or claims.get("personGuid")
            or claims.get("userGuid")
            or claims.get("sub")
        )
        raw_uid = (
            auth_resp.get("userId")
            or account.get("userId")
            or claims.get("userId")
            or claims.get("sbUserId")
        )
        if raw_uid is not None and str(raw_uid).isdigit():
            self.user_id = int(raw_uid)

        raw_mid = (
            auth_resp.get("memberId")
            or account.get("memberId")
            or claims.get("memberId")
            or claims.get("mid")
        )
        if raw_mid is not None:
            self.member_id = str(raw_mid)

        self.first_name = (
            auth_resp.get("firstName")
            or account.get("firstName")
            or claims.get("firstName")
            or claims.get("given_name")
        )
        self.last_name = (
            auth_resp.get("lastName")
            or account.get("lastName")
            or claims.get("lastName")
            or claims.get("family_name")
        )

        # Clear caches
        self.units_cache.clear()
        self.roster_cache.clear()
        self.dens_cache.clear()

        # Enrich profile and units from api.scouting.org
        units = self.get_units()
        return {
            "authenticated": True,
            "demo_mode": False,
            "person_guid": self.person_guid,
            "user_id": self.user_id,
            "member_id": self.member_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "units": units,
        }

    def login_demo(self) -> Dict[str, Any]:
        """Enable built-in Demo Pack mode with realistic sample scouts and adventures."""
        self.demo_mode = True
        self.token = "demo-token"
        self.person_guid = "demo-leader-guid-0001"
        self.user_id = 9000001
        self.member_id = "123456789"
        self.username = "demo.leader"
        self.first_name = "Alex"
        self.last_name = "Leader (Demo)"

        demo_org_guid = "DEMO-PACK-314-GUID"
        self.units_cache = [
            {
                "organization_guid": demo_org_guid,
                "name": "Pack 314 (Demo Cub Scout Pack)",
                "unit_number": "314",
                "program_type": "Cub Scouts",
            }
        ]
        self.dens_cache[demo_org_guid] = [
            {"id": 101, "name": "Lion Den 1", "den_type": "Lion", "den_number": "1"},
            {"id": 102, "name": "Tiger Den 2", "den_type": "Tiger", "den_number": "2"},
            {"id": 103, "name": "Wolf Den 3", "den_type": "Wolf", "den_number": "3"},
            {"id": 104, "name": "Bear Den 4", "den_type": "Bear", "den_number": "4"},
            {"id": 105, "name": "Webelos Den 5", "den_type": "Webelos", "den_number": "5"},
            {"id": 106, "name": "AOL Den 6", "den_type": "AOL", "den_number": "6"},
        ]
        self.roster_cache[demo_org_guid] = [
            ScoutMember(
                user_id=15005204,
                member_id="140882311",
                person_guid="guid-15005204",
                first_name="Liam",
                last_name="Carter",
                nick_name="",
                age=8,
                current_rank="Wolf",
                dens=["Wolf Den 3"],
                den_ids=[103],
            ),
            ScoutMember(
                user_id=15005205,
                member_id="140882312",
                person_guid="guid-15005205",
                first_name="Noah",
                last_name="Martinez",
                nick_name="",
                age=8,
                current_rank="Wolf",
                dens=["Wolf Den 3"],
                den_ids=[103],
            ),
            ScoutMember(
                user_id=15005206,
                member_id="140882313",
                person_guid="guid-15005206",
                first_name="Olivia",
                last_name="Chen",
                nick_name="Liv",
                age=8,
                current_rank="Wolf",
                dens=["Wolf Den 3"],
                den_ids=[103],
            ),
            ScoutMember(
                user_id=15005207,
                member_id="140882314",
                person_guid="guid-15005207",
                first_name="Ethan",
                last_name="Brooks",
                nick_name="",
                age=7,
                current_rank="Tiger",
                dens=["Tiger Den 2"],
                den_ids=[102],
            ),
            ScoutMember(
                user_id=15005208,
                member_id="140882315",
                person_guid="guid-15005208",
                first_name="Sophia",
                last_name="Patel",
                nick_name="",
                age=7,
                current_rank="Tiger",
                dens=["Tiger Den 2"],
                den_ids=[102],
            ),
            ScoutMember(
                user_id=15005209,
                member_id="140882316",
                person_guid="guid-15005209",
                first_name="Mason",
                last_name="Walker",
                nick_name="",
                age=9,
                current_rank="Bear",
                dens=["Bear Den 4"],
                den_ids=[104],
            ),
            ScoutMember(
                user_id=15005210,
                member_id="140882317",
                person_guid="guid-15005210",
                first_name="Ava",
                last_name="Robinson",
                nick_name="",
                age=9,
                current_rank="Bear",
                dens=["Bear Den 4"],
                den_ids=[104],
            ),
            ScoutMember(
                user_id=15005211,
                member_id="140882318",
                person_guid="guid-15005211",
                first_name="Lucas",
                last_name="Kim",
                nick_name="",
                age=10,
                current_rank="Webelos",
                dens=["Webelos Den 5"],
                den_ids=[105],
            ),
            ScoutMember(
                user_id=15005212,
                member_id="140882319",
                person_guid="guid-15005212",
                first_name="Harper",
                last_name="Davis",
                nick_name="",
                age=10,
                current_rank="Arrow of Light",
                dens=["AOL Den 6"],
                den_ids=[106],
            ),
            ScoutMember(
                user_id=15005213,
                member_id="140882320",
                person_guid="guid-15005213",
                first_name="Leo",
                last_name="Nguyen",
                nick_name="",
                age=6,
                current_rank="Lion",
                dens=["Lion Den 1"],
                den_ids=[101],
            ),
        ]
        self._populate_demo_adventures()
        return {
            "authenticated": True,
            "demo_mode": True,
            "person_guid": self.person_guid,
            "user_id": self.user_id,
            "member_id": self.member_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "units": self.units_cache,
        }

    def _populate_demo_adventures(self) -> None:
        self.ranks_cache = [
            {"id": 7, "name": "Lion", "level": 0, "programId": 1},
            {"id": 1, "name": "Bobcat", "level": 1, "programId": 1},
            {"id": 2, "name": "Tiger", "level": 2, "programId": 1},
            {"id": 3, "name": "Wolf", "level": 3, "programId": 1},
            {"id": 4, "name": "Bear", "level": 4, "programId": 1},
            {"id": 5, "name": "Webelos", "level": 5, "programId": 1},
            {"id": 6, "name": "Arrow of Light", "level": 6, "programId": 1},
        ]
        self.adventures_cache = [
            {
                "id": 119,
                "name": "Paws on the Path",
                "rank_id": 3,
                "rank_name": "Wolf",
                "is_required": True,
                "active_version_id": 219,
                "versions": [
                    {
                        "versionId": 219,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
            {
                "id": 115,
                "name": "Wolf Bobcat",
                "rank_id": 3,
                "rank_name": "Wolf",
                "is_required": True,
                "active_version_id": 215,
                "versions": [
                    {
                        "versionId": 215,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
            {
                "id": 120,
                "name": "Council Fire",
                "rank_id": 3,
                "rank_name": "Wolf",
                "is_required": True,
                "active_version_id": 220,
                "versions": [
                    {
                        "versionId": 220,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
            {
                "id": 105,
                "name": "Tigers in the Wild",
                "rank_id": 2,
                "rank_name": "Tiger",
                "is_required": True,
                "active_version_id": 205,
                "versions": [
                    {
                        "versionId": 205,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
            {
                "id": 130,
                "name": "Bear Claws",
                "rank_id": 4,
                "rank_name": "Bear",
                "is_required": True,
                "active_version_id": 230,
                "versions": [
                    {
                        "versionId": 230,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
            {
                "id": 140,
                "name": "Cast Iron Chef",
                "rank_id": 5,
                "rank_name": "Webelos",
                "is_required": True,
                "active_version_id": 240,
                "versions": [
                    {
                        "versionId": 240,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
            {
                "id": 150,
                "name": "Outdoor Adventurer",
                "rank_id": 6,
                "rank_name": "Arrow of Light",
                "is_required": True,
                "active_version_id": 250,
                "versions": [
                    {
                        "versionId": 250,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
            {
                "id": 101,
                "name": "Lion's Honor",
                "rank_id": 7,
                "rank_name": "Lion",
                "is_required": True,
                "active_version_id": 201,
                "versions": [
                    {
                        "versionId": 201,
                        "version": "2024",
                        "versionEffectiveDt": "2024-06-01",
                        "versionExpiryDt": None,
                        "isRequired": True,
                    }
                ],
            },
        ]
        for adv in self.adventures_cache:
            aid = adv["id"]
            vid = adv["active_version_id"]
            self.requirements_cache[(aid, vid)] = [
                {
                    "id": aid * 10 + 1,
                    "requirementNumber": "1",
                    "listNumber": "1",
                    "name": "Requirement 1",
                    "description": f"Identify and prepare the Cub Scout Six Essentials for {adv['name']}.",
                    "required": True,
                },
                {
                    "id": aid * 10 + 2,
                    "requirementNumber": "2",
                    "listNumber": "2",
                    "name": "Requirement 2",
                    "description": "Learn about the Buddy System and explain why we use it on outings.",
                    "required": True,
                },
                {
                    "id": aid * 10 + 3,
                    "requirementNumber": "3",
                    "listNumber": "3",
                    "name": "Requirement 3",
                    "description": "Participate in a den or pack activity demonstrating Leave No Trace principles.",
                    "required": True,
                },
                {
                    "id": aid * 10 + 4,
                    "requirementNumber": "4",
                    "listNumber": "4",
                    "name": "Requirement 4",
                    "description": "Complete the hands-on outdoor challenge with your den or family.",
                    "required": True,
                },
            ]

    # -------------------------------------------------------------------------
    # Units, Roster & Dens
    # -------------------------------------------------------------------------

    def get_units(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Discover all Scouting units the authenticated leader has access to."""
        if self.demo_mode or (self.units_cache and not force_refresh):
            return self.units_cache

        units_by_guid: Dict[str, Dict[str, Any]] = {}

        # 1. Try /persons/v2/{personGuid}/personprofile
        lookup_id = self.person_guid or self.user_id
        if lookup_id:
            try:
                prof_resp = self._request(
                    "GET", f"{API_BASE_URL}/persons/v2/{lookup_id}/personprofile"
                )
                if isinstance(prof_resp, dict):
                    profile = prof_resp.get("profile") or {}
                    if profile.get("userId") and not self.user_id:
                        self.user_id = int(profile["userId"])
                    if profile.get("memberId") and not self.member_id:
                        self.member_id = str(profile["memberId"])
                    if profile.get("personGuid") and not self.person_guid:
                        self.person_guid = str(profile["personGuid"])
                    if profile.get("firstName"):
                        self.first_name = profile["firstName"]
                    if profile.get("lastName"):
                        self.last_name = profile["lastName"]

                    for org in prof_resp.get("organizationPositions") or []:
                        raw_guid = str(org.get("organizationGuid") or "").split("*")[0]
                        if not raw_guid:
                            continue
                        units_by_guid[raw_guid] = {
                            "organization_guid": raw_guid,
                            "name": org.get("organizationName")
                            or org.get("name")
                            or f"Unit {org.get('organizationNumber', '')}".strip(),
                            "unit_number": str(org.get("organizationNumber") or ""),
                            "program_type": org.get("programType") or "",
                        }
            except ScoutingAPIError:
                pass

        # 2. Try /persons/{personGuid}/roleTypes
        if self.person_guid:
            try:
                roles_resp = self._request(
                    "GET",
                    f"{API_BASE_URL}/persons/{self.person_guid}/roleTypes",
                    params={
                        "includeParentRoles": "true",
                        "includeScoutbookRoles": "true",
                    },
                )
                if isinstance(roles_resp, list):
                    for role_item in roles_resp:
                        if not isinstance(role_item, dict):
                            continue
                        raw_guid = str(role_item.get("organizationGuid") or "").split("*")[0]
                        if not raw_guid:
                            continue
                        org_name = (
                            role_item.get("organizationName")
                            or role_item.get("unitName")
                            or units_by_guid.get(raw_guid, {}).get("name")
                            or f"Unit {role_item.get('organizationNumber', '')}".strip()
                        )
                        units_by_guid[raw_guid] = {
                            "organization_guid": raw_guid,
                            "name": org_name,
                            "unit_number": str(
                                role_item.get("organizationNumber")
                                or units_by_guid.get(raw_guid, {}).get("unit_number")
                                or ""
                            ),
                            "program_type": role_item.get("programType")
                            or units_by_guid.get(raw_guid, {}).get("program_type")
                            or "",
                        }
            except ScoutingAPIError:
                pass

        self.units_cache = list(units_by_guid.values())
        return self.units_cache

    def get_unit_roster(
        self, org_guid: str, force_refresh: bool = False
    ) -> Tuple[List[ScoutMember], List[Dict[str, Any]]]:
        """
        Fetch both the youth roster and dens (subUnits) for a unit.
        Combines `/organizations/v2/units/{orgGuid}/youths`, `/subUnits`, and `/subUnitYouths`.
        """
        clean_guid = org_guid.split("*")[0].strip()
        if self.demo_mode:
            return (
                self.roster_cache.get(clean_guid, []),
                self.dens_cache.get(clean_guid, []),
            )

        if (
            not force_refresh
            and clean_guid in self.roster_cache
            and clean_guid in self.dens_cache
        ):
            return self.roster_cache[clean_guid], self.dens_cache[clean_guid]

        # 1. Fetch subUnits (Dens / Patrols)
        sub_units_by_id: Dict[int, Dict[str, Any]] = {}
        dens_list: List[Dict[str, Any]] = []
        try:
            su_resp = self._request(
                "GET", f"{API_BASE_URL}/organizations/v2/units/{clean_guid}/subUnits"
            )
            raw_sub_units = (
                su_resp
                if isinstance(su_resp, list)
                else (su_resp.get("subUnits") or [])
                if isinstance(su_resp, dict)
                else []
            )
            for su in raw_sub_units:
                if not isinstance(su, dict):
                    continue
                su_id = su.get("subUnitId") or su.get("id")
                if su_id is None:
                    continue
                su_id_int = int(su_id)
                den_type = (su.get("denType") or "").strip()
                su_name = (su.get("subUnitName") or su.get("name") or "").strip()
                if den_type and not su_name.lower().startswith(den_type.lower()):
                    display_den = f"{den_type} Den {su_name}".strip()
                else:
                    display_den = su_name or f"Den {su_id_int}"
                entry = {
                    "id": su_id_int,
                    "name": display_den,
                    "raw_name": su_name,
                    "den_type": den_type,
                    "den_number": su_name,
                }
                sub_units_by_id[su_id_int] = entry
                dens_list.append(entry)
        except ScoutingAPIError:
            pass

        # 2. Fetch subUnitYouths mapping (by personGuid)
        guid_to_dens: Dict[str, List[Tuple[Optional[int], str]]] = {}
        try:
            suy_resp = self._request(
                "GET",
                f"{API_BASE_URL}/organizations/v2/units/{clean_guid}/subUnitYouths",
            )
            members = (
                suy_resp.get("members") or []
                if isinstance(suy_resp, dict)
                else suy_resp
                if isinstance(suy_resp, list)
                else []
            )
            for mem in members:
                if not isinstance(mem, dict):
                    continue
                p_guid = str(mem.get("personGuid") or "")
                if not p_guid:
                    continue
                for info in mem.get("subUnitInfo") or []:
                    if not isinstance(info, dict):
                        continue
                    did = info.get("denId") or info.get("patrolId")
                    dtype = (info.get("denType") or "").strip()
                    dnum = (info.get("denNumber") or info.get("patrolName") or "").strip()
                    did_int = int(did) if did is not None and str(did).isdigit() else None
                    if did_int and did_int in sub_units_by_id:
                        dname = sub_units_by_id[did_int]["name"]
                    elif dtype and dnum:
                        dname = f"{dtype} Den {dnum}"
                    elif dtype:
                        dname = f"{dtype} Den"
                    elif dnum:
                        dname = f"Den {dnum}"
                    else:
                        continue
                    guid_to_dens.setdefault(p_guid, []).append((did_int, dname))
                    if did_int and did_int not in sub_units_by_id:
                        entry = {
                            "id": did_int,
                            "name": dname,
                            "raw_name": dnum,
                            "den_type": dtype,
                            "den_number": dnum,
                        }
                        sub_units_by_id[did_int] = entry
                        dens_list.append(entry)
        except ScoutingAPIError:
            pass

        # 3. Fetch Youth Roster
        youths_raw: List[Dict[str, Any]] = []
        try:
            y_resp = self._request(
                "GET", f"{API_BASE_URL}/organizations/v2/units/{clean_guid}/youths"
            )
            if isinstance(y_resp, dict):
                youths_raw = y_resp.get("users") or y_resp.get("members") or []
            elif isinstance(y_resp, list):
                youths_raw = y_resp
        except ScoutingAPIError:
            # Fallback to POST /organizations/v2/{orgGuid}/orgYouths if needed
            y_resp = self._request(
                "POST",
                f"{API_BASE_URL}/organizations/v2/{clean_guid}/orgYouths",
                body={"includeExpired": True, "includeRegistrationDetails": True},
            )
            if isinstance(y_resp, dict):
                youths_raw = y_resp.get("members") or y_resp.get("users") or []

        scouts: List[ScoutMember] = []
        seen_users: set = set()
        for u in youths_raw:
            if not isinstance(u, dict):
                continue
            uid = u.get("userId")
            mid = str(u.get("memberId") or "")
            pguid = str(u.get("personGuid") or "")
            uid_int = int(uid) if uid is not None and str(uid).isdigit() else 0
            key = uid_int or mid or pguid
            if not key or key in seen_users:
                continue
            seen_users.add(key)

            # Resolve Den names and IDs from positions + subUnitYouths
            scout_dens: List[str] = []
            scout_den_ids: List[int] = []

            for pos in u.get("positions") or []:
                if not isinstance(pos, dict):
                    continue
                did = pos.get("denId") or pos.get("patrolId")
                did_int = int(did) if did is not None and str(did).isdigit() else None
                dtype = (pos.get("denType") or "").strip()
                dnum = (pos.get("denNumber") or pos.get("patrolName") or "").strip()
                dname = ""
                if did_int and did_int in sub_units_by_id:
                    dname = sub_units_by_id[did_int]["name"]
                elif dtype and dnum:
                    dname = f"{dtype} Den {dnum}"
                elif dtype:
                    dname = f"{dtype} Den"
                elif dnum:
                    dname = f"Den {dnum}"
                if dname and dname not in scout_dens:
                    scout_dens.append(dname)
                if did_int and did_int not in scout_den_ids:
                    scout_den_ids.append(did_int)
                    if did_int not in sub_units_by_id and dname:
                        entry = {
                            "id": did_int,
                            "name": dname,
                            "raw_name": dnum,
                            "den_type": dtype,
                            "den_number": dnum,
                        }
                        sub_units_by_id[did_int] = entry
                        dens_list.append(entry)

            for did_int, dname in guid_to_dens.get(pguid, []):
                if dname and dname not in scout_dens:
                    scout_dens.append(dname)
                if did_int and did_int not in scout_den_ids:
                    scout_den_ids.append(did_int)

            # Highest rank approved
            highest_rank = ""
            ranks_approved = u.get("highestRanksApproved") or u.get("highestRanksAwarded") or []
            if isinstance(ranks_approved, list) and ranks_approved:
                first_r = ranks_approved[0]
                if isinstance(first_r, dict):
                    highest_rank = str(first_r.get("rank") or first_r.get("name") or "")

            scouts.append(
                ScoutMember(
                    user_id=uid_int,
                    member_id=mid,
                    person_guid=pguid,
                    first_name=str(u.get("firstName") or "").strip(),
                    last_name=str(u.get("lastName") or "").strip(),
                    middle_name=str(u.get("middleName") or "").strip(),
                    nick_name=str(u.get("nickName") or "").strip(),
                    age=int(u["age"]) if u.get("age") is not None and str(u.get("age")).isdigit() else None,
                    current_rank=highest_rank,
                    dens=scout_dens,
                    den_ids=scout_den_ids,
                )
            )

        scouts.sort(key=lambda s: (s.last_name.lower(), s.first_name.lower()))
        self.roster_cache[clean_guid] = scouts
        self.dens_cache[clean_guid] = dens_list
        return scouts, dens_list

    # -------------------------------------------------------------------------
    # Adventures, Ranks & Requirements
    # -------------------------------------------------------------------------

    def get_ranks(self) -> List[Dict[str, Any]]:
        """Fetch ranks dictionary from /advancements/v2/ranks."""
        if self.demo_mode or self.ranks_cache:
            return self.ranks_cache
        try:
            resp = self._request("GET", f"{API_BASE_URL}/advancements/v2/ranks")
            if isinstance(resp, list):
                self.ranks_cache = resp
            elif isinstance(resp, dict) and isinstance(resp.get("ranks"), list):
                self.ranks_cache = resp["ranks"]
        except ScoutingAPIError:
            self.ranks_cache = [
                {"id": k, "name": v} for k, v in CUB_RANK_NAMES.items()
            ]
        return self.ranks_cache

    def get_adventures(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch all Cub Scout adventures and versions from /advancements/v2/adventures."""
        if self.demo_mode or (self.adventures_cache and not force_refresh):
            return self.adventures_cache

        ranks = self.get_ranks()
        rank_map = {
            int(r["id"]): str(r.get("name") or "")
            for r in ranks
            if isinstance(r, dict) and r.get("id") is not None
        }
        for k, v in CUB_RANK_NAMES.items():
            rank_map.setdefault(k, v)

        resp = self._request("GET", f"{API_BASE_URL}/advancements/v2/adventures")
        raw_advs = (
            resp.get("adventures")
            if isinstance(resp, dict) and isinstance(resp.get("adventures"), list)
            else resp
            if isinstance(resp, list)
            else []
        )

        normalized: List[Dict[str, Any]] = []
        for adv in raw_advs:
            if not isinstance(adv, dict) or adv.get("id") is None:
                continue
            adv_id = int(adv["id"])
            rank_id = int(adv["rankId"]) if adv.get("rankId") is not None else None
            rank_name = rank_map.get(rank_id or 0, adv.get("rankName") or "Cub Scout")
            versions = adv.get("versions") or []
            # Sort versions by versionEffectiveDt descending or versionId descending
            sorted_versions = sorted(
                [v for v in versions if isinstance(v, dict)],
                key=lambda v: (
                    v.get("versionExpiryDt") is None,
                    str(v.get("versionEffectiveDt") or ""),
                    int(v.get("versionId") or 0),
                ),
                reverse=True,
            )
            active_version = sorted_versions[0] if sorted_versions else {}
            active_version_id = (
                int(active_version["versionId"])
                if active_version.get("versionId") is not None
                else None
            )
            is_req = bool(
                active_version.get("isRequired")
                if "isRequired" in active_version
                else adv.get("isRequired")
            )
            normalized.append(
                {
                    "id": adv_id,
                    "name": str(adv.get("name") or f"Adventure {adv_id}").strip(),
                    "rank_id": rank_id,
                    "rank_name": rank_name,
                    "is_required": is_req,
                    "image_url": adv.get("imageUrl100") or adv.get("imageUrl") or "",
                    "active_version_id": active_version_id,
                    "versions": sorted_versions,
                }
            )

        normalized.sort(key=lambda a: (a["rank_id"] or 99, not a["is_required"], a["name"].lower()))
        self.adventures_cache = normalized
        return self.adventures_cache

    def get_adventure_requirements(
        self, adventure_id: int, version_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Fetch adventure metadata and requirements for a specific adventure and version.
        """
        advs = self.get_adventures()
        adv_info = next((a for a in advs if a["id"] == int(adventure_id)), None)

        if version_id is None and adv_info:
            version_id = adv_info.get("active_version_id")

        if self.demo_mode:
            reqs = self.requirements_cache.get((int(adventure_id), int(version_id or 0)), [])
            if not reqs and adv_info:
                reqs = self.requirements_cache.get(
                    (int(adventure_id), int(adv_info.get("active_version_id") or 0)), []
                )
            return {
                "adventure": adv_info
                or {
                    "id": int(adventure_id),
                    "name": f"Adventure #{adventure_id}",
                    "rank_name": "Cub Scout",
                    "active_version_id": version_id or 1,
                    "versions": [{"versionId": version_id or 1, "version": "Current"}],
                },
                "version_id": version_id or 1,
                "requirements": reqs,
            }

        cache_key = (int(adventure_id), int(version_id or 0))
        if cache_key not in self.requirements_cache:
            params = {"versionId": version_id} if version_id is not None else {}
            resp = self._request(
                "GET",
                f"{API_BASE_URL}/advancements/adventures/{int(adventure_id)}/requirements",
                params=params,
            )
            raw_reqs = (
                resp.get("requirements")
                if isinstance(resp, dict) and isinstance(resp.get("requirements"), list)
                else resp
                if isinstance(resp, list)
                else []
            )
            if not version_id and isinstance(resp, dict) and resp.get("versionId"):
                version_id = int(resp["versionId"])
                cache_key = (int(adventure_id), version_id)
            self.requirements_cache[cache_key] = self._flatten_requirements(raw_reqs)

        return {
            "adventure": adv_info
            or {
                "id": int(adventure_id),
                "name": f"Adventure #{adventure_id}",
                "rank_name": "Cub Scout",
                "active_version_id": version_id,
                "versions": [{"versionId": version_id, "version": "Current"}] if version_id else [],
            },
            "version_id": version_id,
            "requirements": self.requirements_cache[cache_key],
        }

    def _flatten_requirements(
        self, req_list: List[Dict[str, Any]], parent_prefix: str = ""
    ) -> List[Dict[str, Any]]:
        """Flatten nested childRequirements so every selectable requirement has an ID & label."""
        flat: List[Dict[str, Any]] = []
        for r in req_list:
            if not isinstance(r, dict):
                continue
            req_id = r.get("id")
            num = str(
                r.get("requirementNumber")
                or r.get("listNumber")
                or r.get("number")
                or ""
            ).strip()
            label = f"{parent_prefix}{num}".strip() if parent_prefix else num
            desc = re.sub(r"<[^>]+>", " ", str(r.get("description") or r.get("name") or "")).strip()
            desc = re.sub(r"\s+", " ", desc)
            if req_id is not None:
                flat.append(
                    {
                        "id": int(req_id),
                        "requirementNumber": label or str(req_id),
                        "listNumber": str(r.get("listNumber") or label or req_id),
                        "name": str(r.get("name") or f"Req {label}").strip(),
                        "description": desc,
                        "required": bool(r.get("required", True)),
                    }
                )
            children = r.get("childRequirements") or r.get("requirements") or []
            if isinstance(children, list) and children:
                flat.extend(self._flatten_requirements(children, parent_prefix=f"{label}"))
        return flat

    # -------------------------------------------------------------------------
    # Scout Filtering / Resolution (by Den, Scouting Number, or Name)
    # -------------------------------------------------------------------------

    def resolve_scouts(
        self,
        org_guid: str,
        *,
        user_ids: Optional[List[int]] = None,
        scouting_numbers: Optional[List[str]] = None,
        names: Optional[List[str]] = None,
        dens: Optional[List[str]] = None,
        query_tokens: Optional[List[str]] = None,
    ) -> Tuple[List[ScoutMember], List[str]]:
        """
        Resolve scouts from the unit roster matching any of the provided criteria:
        - user_ids: Scoutbook User IDs (e.g. 15005204)
        - scouting_numbers: BSA Member IDs (memberId) or Scoutbook User IDs (userId)
        - names: Full name, first name, last name, or nickname (case-insensitive)
        - dens: Den name, Den number, or Den rank type (e.g. "Wolf Den 3", "Den 3", "Wolf")
        - query_tokens: Free-form strings that can be a name, scouting number, or den

        Returns (matched_scouts, unmatched_queries).
        """
        roster, _ = self.get_unit_roster(org_guid)
        matched_by_uid: Dict[int, ScoutMember] = {}
        unmatched: List[str] = []

        # 1. Explicit user_ids
        if user_ids:
            uid_set = {int(x) for x in user_ids}
            for s in roster:
                if s.user_id in uid_set:
                    matched_by_uid[s.user_id] = s

        # 2. Explicit scouting_numbers (checks both member_id and user_id)
        for num in scouting_numbers or []:
            clean_num = str(num).strip()
            if not clean_num:
                continue
            found = [
                s
                for s in roster
                if s.member_id == clean_num or str(s.user_id) == clean_num
            ]
            if found:
                for s in found:
                    matched_by_uid[s.user_id] = s
            else:
                unmatched.append(clean_num)

        # 3. Explicit dens
        for den_q in dens or []:
            clean_den = den_q.strip().lower()
            if not clean_den:
                continue
            found = self._match_den(roster, clean_den)
            if found:
                for s in found:
                    matched_by_uid[s.user_id] = s
            else:
                unmatched.append(f"Den '{den_q}'")

        # 4. Explicit names
        for name_q in names or []:
            clean_name = name_q.strip().lower()
            if not clean_name:
                continue
            found = self._match_name(roster, clean_name)
            if found:
                for s in found:
                    matched_by_uid[s.user_id] = s
            else:
                unmatched.append(name_q)

        # 5. Free-form tokens (try number -> den -> name)
        for tok in query_tokens or []:
            clean_tok = tok.strip()
            if not clean_tok:
                continue
            low_tok = clean_tok.lower()
            # Try number
            found = [
                s
                for s in roster
                if s.member_id == clean_tok or str(s.user_id) == clean_tok
            ]
            if not found:
                found = self._match_den(roster, low_tok)
            if not found:
                found = self._match_name(roster, low_tok)
            if found:
                for s in found:
                    matched_by_uid[s.user_id] = s
            else:
                unmatched.append(clean_tok)

        return list(matched_by_uid.values()), unmatched

    @staticmethod
    def _match_den(roster: List[ScoutMember], den_query_lower: str) -> List[ScoutMember]:
        results: List[ScoutMember] = []
        # Normalize e.g. "den 3" -> check if den contains "den 3" or ends with " 3"
        m_num = re.fullmatch(r"den\s*#?\s*(\w+)", den_query_lower)
        target_num = m_num.group(1) if m_num else None

        for s in roster:
            for d in s.dens:
                d_low = d.lower()
                if den_query_lower == d_low or den_query_lower in d_low:
                    results.append(s)
                    break
                if target_num and re.search(rf"\bden\s*#?\s*{re.escape(target_num)}\b", d_low):
                    results.append(s)
                    break
        return results

    @staticmethod
    def _match_name(roster: List[ScoutMember], name_query_lower: str) -> List[ScoutMember]:
        exact: List[ScoutMember] = []
        partial: List[ScoutMember] = []
        for s in roster:
            full_low = s.full_name.lower()
            rev_low = f"{s.last_name}, {s.first_name}".lower()
            first_low = s.first_name.lower()
            last_low = s.last_name.lower()
            nick_low = s.nick_name.lower()
            if name_query_lower in (full_low, rev_low, first_low, last_low, nick_low):
                exact.append(s)
            elif name_query_lower in full_low or name_query_lower in s.display_name.lower():
                partial.append(s)
        return exact if exact else partial

    # -------------------------------------------------------------------------
    # Assigning Adventures & Requirements
    # -------------------------------------------------------------------------

    def assign_adventure(
        self,
        *,
        org_guid: str,
        adventure_id: int,
        user_ids: List[int],
        status: str = "approved",  # "started", "completed", "approved", "awarded"
        mode: str = "whole_adventure",  # "whole_adventure", "all_requirements", "requirements"
        requirement_ids: Optional[List[int]] = None,
        version_id: Optional[int] = None,
        completion_date: Optional[str] = None,
        comment: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Assign/record an adventure or specific adventure requirements for a list of scouts.

        Supported modes:
        - "whole_adventure":
          - If status == "started": POST /advancements/youth/{userId}/adventures
          - If status in ("completed", "approved", "awarded"): POST /advancements/youth/adventures
        - "all_requirements" or "requirements":
          - POST /advancements/v2/youth/adventures/{adventureId}/requirements
        """
        clean_guid = org_guid.split("*")[0].strip()
        date_str = completion_date or dt.date.today().strftime("%Y-%m-%d")
        norm_status = status.strip().lower()
        if norm_status not in ("started", "completed", "approved", "awarded"):
            raise ScoutingAPIError(f"Unsupported status: {status}", status_code=400)

        adv_data = self.get_adventure_requirements(adventure_id, version_id)
        adv_info = adv_data["adventure"]
        resolved_version_id = version_id or adv_data.get("version_id") or adv_info.get("active_version_id")
        all_reqs = adv_data.get("requirements") or []

        # Determine if we are posting requirement-level or whole-adventure-level
        if mode == "all_requirements":
            target_req_ids = [int(r["id"]) for r in all_reqs]
            effective_mode = "requirements"
        elif mode == "requirements" and requirement_ids:
            target_req_ids = [int(x) for x in requirement_ids]
            effective_mode = "requirements"
        else:
            target_req_ids = []
            effective_mode = "whole_adventure"

        if effective_mode == "requirements":
            payload = self._build_requirements_payload(
                org_guid=clean_guid,
                user_ids=user_ids,
                version_id=int(resolved_version_id or 0),
                requirement_ids=target_req_ids,
                status=norm_status,
                date_str=date_str,
                comment=comment,
            )
            endpoint = f"{API_BASE_URL}/advancements/v2/youth/adventures/{int(adventure_id)}/requirements"
            if dry_run or self.demo_mode:
                return {
                    "ok": True,
                    "dry_run": dry_run,
                    "demo_mode": self.demo_mode,
                    "mode": effective_mode,
                    "status": norm_status,
                    "adventure_id": int(adventure_id),
                    "adventure_name": adv_info.get("name"),
                    "version_id": resolved_version_id,
                    "scout_count": len(user_ids),
                    "requirement_count": len(target_req_ids),
                    "endpoint": f"POST {endpoint}",
                    "payload": payload,
                }

            api_resp = self._request("POST", endpoint, body=payload)
            return {
                "ok": True,
                "dry_run": False,
                "demo_mode": False,
                "mode": effective_mode,
                "status": norm_status,
                "adventure_id": int(adventure_id),
                "adventure_name": adv_info.get("name"),
                "version_id": resolved_version_id,
                "scout_count": len(user_ids),
                "requirement_count": len(target_req_ids),
                "endpoint": f"POST {endpoint}",
                "payload": payload,
                "api_response": api_resp,
            }

        # Whole Adventure Mode
        if norm_status == "started":
            requests_desc = []
            responses = []
            for uid in user_ids:
                ep = f"{API_BASE_URL}/advancements/youth/{int(uid)}/adventures"
                body = {
                    "organizationGuid": clean_guid,
                    "id": int(adventure_id),
                    "dateStarted": date_str,
                }
                requests_desc.append({"endpoint": f"POST {ep}", "body": body})
                if not dry_run and not self.demo_mode:
                    responses.append(self._request("POST", ep, body=body))

            return {
                "ok": True,
                "dry_run": dry_run,
                "demo_mode": self.demo_mode,
                "mode": "whole_adventure",
                "status": norm_status,
                "adventure_id": int(adventure_id),
                "adventure_name": adv_info.get("name"),
                "version_id": resolved_version_id,
                "scout_count": len(user_ids),
                "endpoint": f"POST {API_BASE_URL}/advancements/youth/{{userId}}/adventures",
                "payload": requests_desc,
                "api_response": responses if responses else None,
            }

        # Whole Adventure Completed / Approved / Awarded
        leader_uid = self.user_id
        whole_payload = []
        for uid in user_ids:
            adv_entry: Dict[str, Any] = {
                "id": int(adventure_id),
                "dateCompleted": date_str,
                "markedCompletedDate": date_str,
                "awarded": norm_status == "awarded",
            }
            if leader_uid:
                adv_entry["markedCompletedUserId"] = int(leader_uid)
                if norm_status in ("approved", "awarded"):
                    adv_entry["leaderApprovedUserId"] = int(leader_uid)
            whole_payload.append(
                {
                    "organizationGuid": clean_guid,
                    "userId": int(uid),
                    "adventures": [adv_entry],
                }
            )

        endpoint = f"{API_BASE_URL}/advancements/youth/adventures"
        if dry_run or self.demo_mode:
            return {
                "ok": True,
                "dry_run": dry_run,
                "demo_mode": self.demo_mode,
                "mode": "whole_adventure",
                "status": norm_status,
                "adventure_id": int(adventure_id),
                "adventure_name": adv_info.get("name"),
                "version_id": resolved_version_id,
                "scout_count": len(user_ids),
                "endpoint": f"POST {endpoint}",
                "payload": whole_payload,
            }

        api_resp = self._request("POST", endpoint, body=whole_payload)
        return {
            "ok": True,
            "dry_run": False,
            "demo_mode": False,
            "mode": "whole_adventure",
            "status": norm_status,
            "adventure_id": int(adventure_id),
            "adventure_name": adv_info.get("name"),
            "version_id": resolved_version_id,
            "scout_count": len(user_ids),
            "endpoint": f"POST {endpoint}",
            "payload": whole_payload,
            "api_response": api_resp,
        }

    @staticmethod
    def _build_requirements_payload(
        *,
        org_guid: str,
        user_ids: List[int],
        version_id: int,
        requirement_ids: List[int],
        status: str,
        date_str: str,
        comment: Optional[str],
    ) -> List[Dict[str, Any]]:
        payload: List[Dict[str, Any]] = []
        for uid in user_ids:
            reqs_arr: List[Dict[str, Any]] = []
            for req_id in requirement_ids:
                req_obj: Dict[str, Any] = {
                    "id": int(req_id),
                    "started": True,
                }
                if status in ("completed", "approved", "awarded"):
                    req_obj["completed"] = True
                    req_obj["dateCompleted"] = date_str
                    req_obj["markedCompletedDate"] = date_str
                if status in ("approved", "awarded"):
                    req_obj["approved"] = True
                    req_obj["leaderApprovedDate"] = date_str
                if comment and comment.strip():
                    req_obj["comments"] = {
                        "subject": "Quick Entry Comment",
                        "body": comment.strip(),
                    }
                reqs_arr.append(req_obj)

            payload.append(
                {
                    "organizationGuid": org_guid,
                    "userId": int(uid),
                    "versionId": int(version_id),
                    "requirements": reqs_arr,
                }
            )
        return payload
