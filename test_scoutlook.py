"""
Automated test suite for Scoutlook (ScoutingClient, ChatbotAssistant, CLI, and FastAPI Server).
"""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from chatbot import ChatbotAssistant
from cli import main as cli_main
from scouting_client import ScoutingClient, parse_adventure_reference
from server import app


class TestScoutlook(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ScoutingClient()
        self.client.login_demo()
        self.org_guid = self.client.get_units()[0]["organization_guid"]

    def test_parse_adventure_reference(self) -> None:
        ref1 = parse_adventure_reference(
            "https://advancements.scouting.org/youthProfile/15005204/adventures/119"
        )
        self.assertEqual(ref1["adventure_id"], 119)
        self.assertEqual(ref1["user_id"], 15005204)

        ref2 = parse_adventure_reference("https://advancements.scouting.org/adventures/130")
        self.assertEqual(ref2["adventure_id"], 130)
        self.assertIsNone(ref2["user_id"])

        ref3 = parse_adventure_reference("119")
        self.assertEqual(ref3["adventure_id"], 119)
        self.assertIsNone(ref3["user_id"])

    def test_resolve_scouts_by_name_number_and_den(self) -> None:
        # 1. By Scoutbook User ID (15005204) and BSA Member ID (140882312)
        matched, unmatched = self.client.resolve_scouts(
            self.org_guid,
            scouting_numbers=["15005204", "140882312", "999999999"],
        )
        self.assertEqual({s.user_id for s in matched}, {15005204, 15005205})
        self.assertEqual(unmatched, ["999999999"])

        # 2. By Name and Nickname
        matched_names, _ = self.client.resolve_scouts(
            self.org_guid,
            names=["Liam Carter", "Liv"],
        )
        self.assertEqual({s.user_id for s in matched_names}, {15005204, 15005206})

        # 3. By Den ("Wolf Den 3" and "Den 2")
        matched_dens, _ = self.client.resolve_scouts(
            self.org_guid,
            dens=["Wolf Den 3"],
        )
        self.assertEqual(
            {s.user_id for s in matched_dens},
            {15005204, 15005205, 15005206},
        )

    def test_assign_whole_adventure_payloads(self) -> None:
        # Whole adventure: approved
        res_approved = self.client.assign_adventure(
            org_guid=self.org_guid,
            adventure_id=119,
            user_ids=[15005204, 15005205],
            status="approved",
            mode="whole_adventure",
            completion_date="2026-10-29",
            dry_run=True,
        )
        self.assertTrue(res_approved["ok"])
        self.assertEqual(
            res_approved["endpoint"],
            "POST https://api.scouting.org/advancements/youth/adventures",
        )
        payload = res_approved["payload"]
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["userId"], 15005204)
        self.assertEqual(payload[0]["adventures"][0]["id"], 119)
        self.assertEqual(payload[0]["adventures"][0]["dateCompleted"], "2026-10-29")
        self.assertEqual(payload[0]["adventures"][0]["leaderApprovedUserId"], 9000001)
        self.assertFalse(payload[0]["adventures"][0]["awarded"])

        # Whole adventure: started
        res_started = self.client.assign_adventure(
            org_guid=self.org_guid,
            adventure_id=119,
            user_ids=[15005204],
            status="started",
            mode="whole_adventure",
            completion_date="2026-10-29",
            dry_run=True,
        )
        self.assertEqual(
            res_started["payload"][0]["endpoint"],
            "POST https://api.scouting.org/advancements/youth/15005204/adventures",
        )
        self.assertEqual(
            res_started["payload"][0]["body"],
            {
                "organizationGuid": self.org_guid,
                "id": 119,
                "dateStarted": "2026-10-29",
            },
        )

    def test_assign_requirements_payload(self) -> None:
        res_reqs = self.client.assign_adventure(
            org_guid=self.org_guid,
            adventure_id=119,
            user_ids=[15005204],
            status="approved",
            mode="requirements",
            requirement_ids=[1191, 1192],
            version_id=219,
            completion_date="2026-10-29",
            comment="Great job on hike",
            dry_run=True,
        )
        self.assertEqual(
            res_reqs["endpoint"],
            "POST https://api.scouting.org/advancements/v2/youth/adventures/119/requirements",
        )
        entry = res_reqs["payload"][0]
        self.assertEqual(entry["userId"], 15005204)
        self.assertEqual(entry["versionId"], 219)
        self.assertEqual(len(entry["requirements"]), 2)
        self.assertTrue(entry["requirements"][0]["started"])
        self.assertTrue(entry["requirements"][0]["completed"])
        self.assertTrue(entry["requirements"][0]["approved"])
        self.assertEqual(
            entry["requirements"][0]["comments"]["body"], "Great job on hike"
        )

    def test_chatbot_assistant(self) -> None:
        bot = ChatbotAssistant(self.client)
        resp = bot.process_message(
            "Assign https://advancements.scouting.org/youthProfile/15005204/adventures/119 to Wolf Den 3 as approved",
            org_guid=self.org_guid,
        )
        self.assertIn("Staged Adventure Assignment", resp["reply"])
        self.assertEqual(resp["ui_state"]["adventure_id"], 119)
        self.assertEqual(
            set(resp["ui_state"]["user_ids"]), {15005204, 15005205, 15005206}
        )
        self.assertIsNotNone(resp.get("preview"))

        # Confirm the staged action
        confirm_resp = bot.process_message("confirm", org_guid=self.org_guid)
        self.assertIn("Submitted **Paws on the Path**", confirm_resp["reply"])
        self.assertTrue(confirm_resp["executed"]["ok"])

    def test_fastapi_endpoints(self) -> None:
        tc = TestClient(app)
        # Login demo
        r_login = tc.post("/api/login", json={"demo": True})
        self.assertEqual(r_login.status_code, 200)
        self.assertTrue(r_login.json()["ok"])

        # Parse URL
        r_parse = tc.post(
            "/api/parse-url",
            json={
                "reference": "https://advancements.scouting.org/youthProfile/15005204/adventures/119",
                "org_guid": "DEMO-PACK-314-GUID",
            },
        )
        self.assertEqual(r_parse.status_code, 200)
        self.assertEqual(r_parse.json()["adventure_id"], 119)
        self.assertEqual(r_parse.json()["user_id"], 15005204)

        # Chat endpoint
        r_chat = tc.post(
            "/api/chat",
            json={
                "message": "Assign Paws on the Path requirements 1, 2 to Liam Carter and 140882312 as completed",
                "org_guid": "DEMO-PACK-314-GUID",
            },
        )
        self.assertEqual(r_chat.status_code, 200)
        chat_data = r_chat.json()
        self.assertTrue(chat_data["ok"])
        self.assertEqual(chat_data["ui_state"]["mode"], "requirements")
        self.assertEqual(chat_data["ui_state"]["requirement_ids"], [1191, 1192])
        self.assertEqual(set(chat_data["ui_state"]["user_ids"]), {15005204, 15005205})

    def test_cli_dry_run(self) -> None:
        rc = cli_main(
            [
                "--demo",
                "--adventure",
                "https://advancements.scouting.org/youthProfile/15005204/adventures/119",
                "--den",
                "Wolf Den 3",
                "--status",
                "approved",
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
