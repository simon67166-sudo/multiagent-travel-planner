import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"orchestrator"))
import schedule_widgets
import trip_plan
import nearby_sources

class IntegrationTests(unittest.TestCase):
    def test_airports_unrestricted_but_local_places_keep_city(self):
        trip=trip_plan.new_trip_plan("test")
        trip_plan.add_flight(trip,{"from_":"Shanghai Airport","to":"Hong Kong Airport"})
        trip_plan.add_hotel(trip,{"name":"Hotel","address":"Kowloon Hotel"})
        with patch.object(schedule_widgets.map_tool,"geocode",return_value=(114.1,22.3)) as geo:
            schedule_widgets.build_trip_map_widget(trip,city="香港")
        geo.assert_any_call("Shanghai Airport",city=None)
        geo.assert_any_call("Hong Kong Airport",city=None)
        geo.assert_any_call("Kowloon Hotel",city="香港")

    def test_nearby_origin_uses_amap_and_gcj(self):
        raw={"_fetched_at":"test","pois":[{"id":"poi1","name":"大三巴","location":"113.54,22.19"}]}
        with patch.object(nearby_sources,"_amap",return_value=raw) as api:
            origin=nearby_sources.find_origin("澳門","大三巴")
        self.assertEqual(origin["crs"],"GCJ02")
        self.assertEqual(api.call_args.args[0],"place/text")
        self.assertEqual(api.call_args.args[1]["city"],"澳門")

    def test_http_map_uses_session_city(self):
        import server
        state={"city":"香港","trip_plan":trip_plan.new_trip_plan("test")}
        with server.app.test_client() as client, patch.object(server.session_store,"load",return_value=state), patch.object(server.schedule_widgets,"build_trip_map_widget",return_value={}) as build:
            self.assertEqual(client.get("/trip").status_code,200)
        self.assertEqual(build.call_args.kwargs["city"],"香港")
