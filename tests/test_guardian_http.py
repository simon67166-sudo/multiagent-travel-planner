import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"orchestrator"))
import server
from session_store import SessionStore
from guardian_store import GuardianStore

class GuardianHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.sessions=SessionStore(Path(self.temp.name)/"sessions.db")
        self.p=patch.object(server,"session_store",self.sessions);self.p.start()
        server.app.config["TESTING"]=True
        self.owner=server.app.test_client();self.member=server.app.test_client()
    def tearDown(self): self.p.stop();self.temp.cleanup()
    def join(self):
        self.owner.get("/team")
        invitation=self.owner.post("/team/invite",json={}).json["invite_url"]
        token=invitation.split("join=")[1]
        response=self.member.post("/team/join",json={"token":token,"name":"同行者"})
        self.assertEqual(response.status_code,200,response.json)
        return response.json
    def test_join_origin_preferences_and_permissions(self):
        member=self.join()
        response=self.member.patch("/team/preferences",json={"preferences":{"diet":["vegan"]},"expected_revision":member["revision"]})
        self.assertEqual(response.status_code,200,response.json)
        team=self.owner.get("/team").json
        self.assertEqual(team["members"][0]["preferences"],{})
        self.assertEqual(team["members"][1]["preferences"]["diet"],["vegan"])
        self.assertEqual(self.member.post("/team/invite",json={}).status_code,403)
        self.assertEqual(self.member.patch("/team/preferences",json={"member_id":team["members"][0]["id"],"preferences":{},"expected_revision":team["revision"]}).status_code,400)
        self.assertEqual(self.owner.post("/team/invite",json={},headers={"Origin":"https://evil.example"}).status_code,403)
    def test_draft_reject_and_accept_never_bypass(self):
        self.join()
        out=self.owner.post("/guardian/plan",json={"message":"多人文化拍照行程","mode":"demo","city":"香港"})
        self.assertEqual(out.status_code,200,out.json)
        proposal=out.json["proposal"]
        self.assertEqual(self.owner.get("/team").json["version"],0)
        url="/proposals/"+proposal["id"]
        self.assertEqual(self.member.post(url+"/accept",json={"expected_version":0}).status_code,403)
        self.assertEqual(self.owner.post(url+"/reject",json={}).status_code,200)
        self.assertEqual(self.owner.get("/team").json["version"],0)
        out=self.owner.post("/guardian/plan",json={"message":"行程","mode":"demo","city":"香港"})
        proposal=out.json["proposal"]
        self.assertEqual(self.owner.post("/proposals/"+proposal["id"]+"/accept",json={"expected_version":0}).status_code,200)
        self.assertEqual(self.member.get("/team").json["version"],1)
        self.assertEqual(self.owner.get("/trip").json["version"],1)
    def test_changed_preferences_invalidate_draft(self):
        team=self.owner.get("/team").json
        out=self.owner.post("/guardian/plan",json={"message":"行程","mode":"demo","city":"澳門"})
        self.assertEqual(out.status_code,200,out.json)
        p=out.json["proposal"]
        self.owner.patch("/team/preferences",json={"preferences":{},"expected_revision":team["revision"]})
        self.assertEqual(self.owner.post("/proposals/"+p["id"]+"/accept",json={"expected_version":0}).status_code,409)
    def test_events_are_cached_for_all_members(self):
        self.join()
        with patch("guardian_service.check_events",return_value={"events":[{"id":"demo-test","data_kind":"demo"}],"proposed_itinerary":None}) as check:
            a=self.owner.post("/events/check",json={"demo":True})
            b=self.member.post("/events/check",json={"demo":True})
        self.assertEqual(a.status_code,200,a.json)
        self.assertTrue(b.json["cached"])
        self.assertEqual(check.call_count,1)
    def test_public_invitation_base(self):
        with patch.dict("os.environ",{"PUBLIC_BASE_URL":"https://travel.example"}):
            response=self.owner.post("/team/invite",json={})
        self.assertTrue(response.json["invite_url"].startswith("https://travel.example/?join="))
    def test_skill_list_has_all_fifteen(self):
        response=self.owner.get("/guardian/skills")
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(response.json["skills"]),15)

if __name__=="__main__":unittest.main()
