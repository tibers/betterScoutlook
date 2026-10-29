# Scoutlook — Scoutbook Plus (`advancements.scouting.org`) Adventure Assigner

**This project is not affiliated with scouting.org, Boy Scouts of America, or any of their affiliates.** This tool is a third-party helper for leaders to quickly assign adventures to scouts in Scoutbook Plus. [It uses the Scoutlook API, which is unsupported.](https://discussions.scouting.org/t/is-there-documentation-for-api-scouting-org/328234) The tool may break at any time so no warrenty is implied, and use at your own risk.

**But why?** The workflow on scouting.org is exactly backwards: I don't want to click on scouts 30 times to assign an adventure, I want to plan an adventure and then assign 30 scouts to it. This was written with [Gemini Argon](https://aistudio.google.com/docs/models/gemini-4-argon). 

**Why do you need my credentials?** The tool starts in Demo Mode with a fake scout and a fake pack because I needed a way to test it. To actually use the tool with your pack and their information, click SIGN IN in the top right corner. Enter your credentials and the tool will use them to interact with your pack. This tool runs locally so no information is stored outside of your computer and the tool is open source so you may inspect the code. The information is the same information for scouts, dens, adventures, and requirements which already exists in scoutlook.

**There's a chatbot?** There's a rudimentary chat available on the scouting API and so I had Gemini add some python around that. This can perform actions on your behalf or it can explain things available on the website. The chat inferface is not backed by any cloud LLM. 

**Scoutlook** is a Web UI, Natural-Language Chatbot Assistant, and CLI for Scouting America leaders to select Cub Scout adventures (by pasting an `advancements.scouting.org` URL/ID or browsing by Rank) and bulk-assign them to scouts based on **Name**, **Scouting Number** (BSA `memberId` or Scoutbook `userId`), or **Den**.

---

## Features

- **Select Adventures Any Way You Want**:
  - **Paste a Scoutbook Plus URL or ID**: Supports full youth profile URLs (e.g., `https://advancements.scouting.org/youthProfile/15005204/adventures/119`), adventure URLs (`https://advancements.scouting.org/adventures/119`), or numeric IDs (`119`).
  - **Browse by Cub Scout Rank**: Filter and select adventures across `Lion`, `Bobcat`, `Tiger`, `Wolf`, `Bear`, `Webelos`, and `Arrow of Light`, including curriculum version selection (`versionId`).
- **Target Scouts by Den, Name, or Scouting Number**:
  - **Den Quick-Select Pills**: One-click selection for any Den in your pack (e.g., `Wolf Den 3`, `Tiger Den 2`).
  - **Smart Multi-Identifier Matching**: Paste a comma- or newline-separated list of **Scout Names** (`Liam Carter`), **BSA Member IDs** (`140882311`), **Scoutbook User IDs** (`15005204`), or **Den Names** (`Wolf Den 3`) to automatically resolve and check those scouts.
  - **Interactive Roster Table**: Live search and checkbox selection across your unit's youth roster.
- **Flexible Recording Scope & Status**:
  - **Scopes**:
    1. **Entire Adventure** (`whole_adventure`)
    2. **All Requirements** (`all_requirements`)
    3. **Specific Requirements** (`requirements` — pick individual requirement checkboxes such as `1`, `2`, `3`)
  - **Statuses**:
    - `Started`
    - `Completed` (Unapproved)
    - `Completed + Leader Approved` (Default)
    - `Completed + Leader Approved + Awarded`
- **Dual Interface (Visual Web UI + Live-Synced Chatbot)**:
  - Use visual point-and-click controls on the left or natural-language commands in the Chatbot panel on the right—both stay synchronized in real time.
- **Dry-Run API Payload Inspector & Built-In Demo Pack**:
  - Preview the exact HTTP endpoint and JSON payload before sending anything to `api.scouting.org`.
  - Includes a built-in **Demo Mode** (`Pack 314`) preloaded with sample scouts (including `15005204`) and adventures (including `#119 Paws on the Path`) for safe offline testing.

---

## Project Structure

```text
scoutlook/
├── scouting_client.py   # API client for auth.scouting.org & api.scouting.org + scout resolver
├── chatbot.py           # Natural-language command parser & conversational assistant
├── server.py            # FastAPI web server & REST endpoints
├── static/
│   └── index.html       # Single-page Web UI + integrated Chatbot panel
├── cli.py               # Command-line utility & terminal interactive chat
├── test_scoutlook.py    # Automated unit & integration test suite
├── requirements.txt     # Python dependencies
└── README.md            # Documentation
```

---

## Installation & Quick Start

### 1. Install Dependencies
Python 3.9+ is required. Note that `scouting_client.py` and `cli.py` use only the Python standard library; `fastapi` and `uvicorn` are used for the web server:

```bash
pip install -r requirements.txt
```

### 2. Start the Web Server
```bash
python3 server.py
```
Open **`http://127.0.0.1:8080`** in your browser (or set `PORT=3000 python3 server.py` to customize the port).

### 3. Sign In or Use Demo Mode
- **Demo Mode (Default on startup)**: Preloads **Pack 314 (Demo Cub Scout Pack)** so you can immediately test pasting `https://advancements.scouting.org/youthProfile/15005204/adventures/119`, clicking Den pills, or chatting with the bot.
- **Live Scouting.org Account**: Click **Sign In (`scouting.org`)** in the top-right navigation bar and enter your My.Scouting / Scoutbook Plus username and password (or paste an active Bearer JWT token).
  - *Security Note*: Passwords are sent directly to `https://auth.scouting.org/api/users/{username}/authenticate` over HTTPS and are never logged or written to disk.

---

## Using the Chatbot Assistant

In the Web UI's right-hand panel (or via `python3 cli.py --chat`), you can type natural-language commands such as:

- **Assign an Adventure by URL to an Entire Den**:
  ```text
  Assign https://advancements.scouting.org/youthProfile/15005204/adventures/119 to Wolf Den 3 as approved
  ```
- **Assign by Adventure Name to Specific Scout Names & Numbers**:
  ```text
  Assign Paws on the Path to Liam Carter, 140882312, 15005206 as completed
  ```
- **Assign Specific Requirements on a Specific Date**:
  ```text
  Mark Paws on the Path requirements 1, 2 for Wolf Den 3 as approved on 2026-10-29
  ```
- **Start or Award an Adventure**:
  ```text
  Start adventure 119 for 15005204
  Award Cast Iron Chef to Webelos Den 5
  ```
- **Inspect Roster, Dens, & Adventures**:
  ```text
  list dens
  who is in Wolf Den 3?
  list Wolf adventures
  ```
- **Confirm a Staged Assignment**:
  ```text
  confirm
  ```

---

## Using the Command-Line Interface (`cli.py`)

You can also list rosters, inspect adventures, or assign advancements directly from your terminal:

```bash
# 1. List unit dens and youth roster (with BSA Member IDs and Scoutbook User IDs):
python3 cli.py --username YOUR_USERNAME --list-roster

# 2. List all Cub Scout adventures and IDs:
python3 cli.py --demo --list-adventures

# 3. Assign an adventure URL to a Den (with --dry-run to preview the JSON payload):
python3 cli.py --username YOUR_USERNAME \
  --adventure "https://advancements.scouting.org/youthProfile/15005204/adventures/119" \
  --den "Wolf Den 3" \
  --status approved \
  --dry-run

# 4. Assign specific requirements (1 and 2) to scouts by Name and BSA Member ID:
python3 cli.py --username YOUR_USERNAME \
  --adventure 119 \
  --requirements 1,2 \
  --scouts "Liam Carter,140882312" \
  --status approved \
  --comment "Completed at Saturday Pack Hike"

# 5. Launch the interactive terminal chatbot:
python3 cli.py --username YOUR_USERNAME --chat
```

---

## `advancements.scouting.org` API Reference

Scoutlook interacts with the official backend services backing Scoutbook Plus (`https://auth.scouting.org` and `https://api.scouting.org`):

| Action | Method & Endpoint | Key Request / Response Details |
| :--- | :--- | :--- |
| **Authenticate** | `POST https://auth.scouting.org/api/users/{username}/authenticate` | Header: `Accept: application/json; version = 2`<br>Body: `{"password": "..."}`<br>Returns JWT `token`, `personGuid`, `userId`, `memberId`. |
| **Leader Profile & Units** | `GET https://api.scouting.org/persons/v2/{personGuid}/personprofile`<br>`GET https://api.scouting.org/persons/{personGuid}/roleTypes` | Returns leader profile and `organizationPositions` containing each unit's `organizationGuid`. |
| **Unit Youth Roster** | `GET https://api.scouting.org/organizations/v2/units/{orgGuid}/youths` | Returns `{ "users": [...] }` with `userId` (Scoutbook ID), `memberId` (BSA ID), `personGuid`, `firstName`, `lastName`, and `positions`. |
| **Unit Dens (SubUnits)** | `GET https://api.scouting.org/organizations/v2/units/{orgGuid}/subUnits`<br>`GET https://api.scouting.org/organizations/v2/units/{orgGuid}/subUnitYouths` | Returns Dens (`subUnitId`, `denType`, `subUnitName`) and maps each youth `personGuid` to their assigned Den(s). |
| **Ranks & Adventures** | `GET https://api.scouting.org/advancements/v2/ranks`<br>`GET https://api.scouting.org/advancements/v2/adventures` | Returns all Cub Scout ranks and adventures with their `versions` (`versionId`, `versionEffectiveDt`, `isRequired`). |
| **Adventure Requirements** | `GET https://api.scouting.org/advancements/adventures/{adventureId}/requirements?versionId={versionId}` | Returns the requirement tree (`id`, `requirementNumber`, `listNumber`, `description`) for the specified adventure and version. |
| **Start Whole Adventure** | `POST https://api.scouting.org/advancements/youth/{userId}/adventures` | Body:<br>`{"organizationGuid": "<orgGuid>", "id": 119, "dateStarted": "YYYY-MM-DD"}` |
| **Complete / Approve / Award Whole Adventure** | `POST https://api.scouting.org/advancements/youth/adventures` | Body:<br>`[{"organizationGuid": "<orgGuid>", "userId": 15005204, "adventures": [{"id": 119, "dateCompleted": "YYYY-MM-DD", "markedCompletedDate": "YYYY-MM-DD", "markedCompletedUserId": <leaderUserId>, "leaderApprovedUserId": <leaderUserId>, "awarded": false}]}]` |
| **Record Requirements** | `POST https://api.scouting.org/advancements/v2/youth/adventures/{adventureId}/requirements` | Body:<br>`[{"organizationGuid": "<orgGuid>", "userId": 15005204, "versionId": 219, "requirements": [{"id": 1191, "started": true, "completed": true, "dateCompleted": "YYYY-MM-DD", "markedCompletedDate": "YYYY-MM-DD", "approved": true, "leaderApprovedDate": "YYYY-MM-DD"}]}]` |

---

## Running Tests

Run the automated test suite to verify URL parsing, scout resolution (by Name, BSA Member ID, Scoutbook User ID, and Den), payload generation, chatbot natural-language parsing, CLI execution, and FastAPI endpoints:

```bash
python3 test_scoutlook.py -v
```

---

## Licensing

This project is dual-licensed:

1. **Open Source Edition:** For the public, this software is available under the terms of the GNU General Public License v3.0 (GPLv3). See the `LICENSE.md` file for details.
2. **Commercial / Proprietary Edition:** If you are the API provider or an entity wishing to embed, modify, or distribute this tool within a closed-source or proprietary environment, please contact the author directly to arrange a custom, proprietary license or a full copyright assignment. I am 100% willing to donate this tool for the good of the organization.

By dual-licensing, the community benefits from an open-source tool, while the platform operator retains a clear legal path to adopt and integrate this tool into their official, closed-source offerings.
