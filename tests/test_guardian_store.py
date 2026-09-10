import sys, tempfile, unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
from guardian_store import GuardianStore, Conflict, Forbidden, canonical_itinerary

class GuardianStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.db"
        self.store = GuardianStore(self.path)
        self.owner = "owner-secret"
        self.team = self.store.ensure(self.owner, {"city":"澳門"})
    def tearDown(self): self.temp.cleanup()
    def invite(self, sid="member-secret"):
        token = self.store.invite(self.owner)
        return self.store.join(sid, token, "Member")
    def test_group_members_and_reload(self):
        token=self.store.invite(self.owner)
        for i in range(25): self.store.join(str(i),token,str(i))
        team=GuardianStore(self.path).snapshot(self.owner)
        self.assertEqual(len(team["members"]),26)
        self.assertNotIn("secret",str(team))
        self.assertEqual(team["role"],"owner")
    def test_only_own_preferences_and_conflicts(self):
        member=self.invite()
        prefs={"diet":["no_spicy"]}
        self.store.preferences("member-secret",prefs,member["revision"])
        snap=self.store.snapshot(self.owner)
        self.assertEqual(snap["members"][0]["preferences"],{})
        self.assertEqual(snap["members"][1]["preferences"],prefs)
        with self.assertRaises(Conflict): self.store.preferences(self.owner,{},member["revision"])
        with self.assertRaises(Forbidden): self.store.invite("member-secret")
    def test_revoke_and_remove(self):
        token=self.store.invite(self.owner)
        member=self.store.join("m",token,"M")
        self.store.invite(self.owner,revoke=True)
        with self.assertRaises(Forbidden): self.store.join("other",token,"Other")
        self.store.remove(self.owner,member["member_id"])
        with self.assertRaises(Forbidden): self.store.snapshot("m")
    def proposal(self):
        snap=self.store.snapshot(self.owner)
        itinerary=snap["itinerary"]
        itinerary["trip_plan"]["hotels"]=[{"name":"演示酒店","currency":"MOP"}]
        return self.store.propose(self.owner,itinerary,"test",snap["version"],snap["revision"])
    def test_proposal_owner_atomic_accept(self):
        self.invite()
        p=self.proposal()
        with self.assertRaises(Forbidden): self.store.accept("member-secret",p["id"],0)
        self.assertEqual(self.store.snapshot(self.owner)["itinerary"]["trip_plan"]["hotels"],[])
        self.store.accept(self.owner,p["id"],0)
        with self.assertRaises(Conflict): self.store.accept(self.owner,p["id"],0)
        self.assertEqual(GuardianStore(self.path).snapshot(self.owner)["version"],1)
    def test_reject_and_stale_preferences(self):
        p=self.proposal()
        self.store.reject(self.owner,p["id"])
        self.assertEqual(self.store.snapshot(self.owner)["version"],0)
        p=self.proposal()
        self.store.preferences(self.owner,{"diet":["vegan"]},self.store.snapshot(self.owner)["revision"])
        with self.assertRaises(Conflict): self.store.accept(self.owner,p["id"],0)
    def test_cross_connection_accept_race(self):
        a,b=self.proposal(),self.proposal()
        def accept(p):
            try: GuardianStore(self.path).accept(self.owner,p["id"],0); return True
            except Conflict: return False
        with ThreadPoolExecutor(2) as pool: result=list(pool.map(accept,[a,b]))
        self.assertEqual(sum(result),1)
    def test_migration_preserves_bookings_and_osm(self):
        legacy={"city":"香港","trip_plan":{"trip_id":"old","days":{},"hotels":[{"name":"H"}],"flights":[],"weather_alerts":[]},"nearby_plan":{"crs":"WGS84","origin":{"lat":22.3,"lon":114.1},"stops":[],"request":{"city":"香港"}}}
        result=canonical_itinerary(legacy)
        self.assertEqual(result["trip_plan"]["hotels"][0]["name"],"H")
        self.assertEqual(result["legacy_nearby_plan"]["crs"],"WGS84")
        self.assertIsNone(result["nearby_plan"])
        self.assertTrue(result["migration_notes"])
    def test_events_deduplicate(self):
        snap=self.store.snapshot(self.owner)
        event={"id":"demo-rain","type":"weather","data_kind":"demo"}
        self.store.record_events(self.owner,[event,event])
        self.store.record_events(self.owner,[event])
        self.assertEqual(len(self.store.snapshot(self.owner)["events"]),1)

if __name__=="__main__": unittest.main()
