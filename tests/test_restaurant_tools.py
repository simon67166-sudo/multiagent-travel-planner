import unittest

from restaurant_tools import search_demo_restaurants


class RestaurantRulesTests(unittest.TestCase):
    def test_all_constraints(self):
        result = search_demo_restaurants(100, True, 20)
        names = [item["name"] for item in result["eligible"]]
        self.assertEqual(names, ["演示餐廳 A"])

        excluded = {
            item["name"]: item["reasons"]
            for item in result["excluded"]
        }
        self.assertIn("超出排隊時間上限", excluded["演示餐廳 B"])
        self.assertIn("沒有不辣選項", excluded["演示餐廳 C"])

    def test_no_feasible_restaurant(self):
        result = search_demo_restaurants(60, True, 20)
        self.assertEqual(result["eligible"], [])

    def test_exact_boundaries_are_allowed(self):
        result = search_demo_restaurants(65, True, 10)
        self.assertEqual(
            [item["name"] for item in result["eligible"]],
            ["演示餐廳 A"],
        )

    def test_unspecified_queue_limit(self):
        result = search_demo_restaurants(100, True, None)
        self.assertEqual(len(result["eligible"]), 2)

    def test_invalid_inputs(self):
        for budget in (-1, True, float("nan")):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    search_demo_restaurants(budget, True, 20)

        with self.assertRaises(ValueError):
            search_demo_restaurants(100, "false", 20)


if __name__ == "__main__":
    unittest.main()