import sys,unittest,copy
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"orchestrator"))
from guardian_replan import rebuild
from guardian_service import validate_itinerary

class ReplanTests(unittest.TestCase):
    def plan(self):
        return {"city":"香港","trip_plan":{"trip_id":"x","flights":[],"hotels":[],"weather_alerts":[],"days":{}},"nearby_plan":{"crs":"GCJ02","origin":{"id":"o","name":"O","lat":22.3,"lng":114.1},"request":{"city":"香港","date":"2026-09-12","start_time":"10:00","hours":3},"stops":[{"id":"a","name":"A","lat":22.31,"lng":114.11,"suggested_stay_minutes":30},{"id":"b","name":"B","lat":22.32,"lng":114.12,"suggested_stay_minutes":40}]}}
    def test_actual_legs_and_nonmutation(self):
        p=self.plan();old=copy.deepcopy(p)
        route=Mock(return_value={"available":True,"duration_min":10,"distance_m":500,"coordinates":[[114.1,22.3],[114.11,22.31]]})
        result=rebuild(p,route)
        self.assertEqual(p,old)
        self.assertEqual(result["nearby_plan"]["stops"][1]["arrival_time"],"10:50")
        self.assertEqual(route.call_args.args[0]["id"],"a")
        self.assertTrue(result["nearby_plan"]["travel_times_available"])
    def test_failure_propagates_unknown_never_fake_route(self):
        route=Mock(side_effect=[{"available":False,"coordinates":[]},{"available":True,"duration_min":10,"distance_m":50,"coordinates":[]}])
        result=rebuild(self.plan(),route)["nearby_plan"]
        self.assertFalse(result["travel_times_available"])
        self.assertTrue(all(s["end_time"]=="待確認" for s in result["stops"]))
        self.assertEqual(result["legs"][0]["coordinates"],[])
    def test_overtime_rejected(self):
        p=self.plan();p["nearby_plan"]["request"]["hours"]=1
        result=rebuild(p,Mock(return_value={"available":True,"duration_min":50}))
        self.assertTrue(validate_itinerary(result,[])["violations"])
    def test_two_days_not_time_overlap(self):
        p=self.plan();p["nearby_plan"]=None
        for day in ['2026-09-12','2026-09-13']:
            p['trip_plan']['days'][day]={'date':day,'head_id':'a','nodes':{'a':{'place':'A','arrival_time':'10:00','end_time':'11:00','next_id':None}}}
        self.assertFalse(validate_itinerary(p,[])["violations"])
    def test_rebuild_preserves_other_day(self):
        p=self.plan()
        other={'date':'2026-09-13','head_id':'z','nodes':{'z':{'place':'Z','arrival_time':'10:00','end_time':'11:00','next_id':None}}}
        p['trip_plan']['days']['2026-09-13']=copy.deepcopy(other)
        result=rebuild(p,Mock(return_value={"available":True,"duration_min":10}))
        self.assertEqual(result['trip_plan']['days']['2026-09-13'],other)
        self.assertEqual(len(result['trip_plan']['days']),2)

    def test_demo_never_queries_provider(self):
        p=self.plan();p['mode']='demo';route=Mock()
        self.assertEqual(rebuild(p,route)['nearby_plan']['legs'],[])
        route.assert_not_called()

if __name__=='__main__':unittest.main()
