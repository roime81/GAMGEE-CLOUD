import unittest

from tower.main import app


class ResourceApiRegistrationTests(unittest.TestCase):
    def test_resource_engine_routes_are_registered(self):
        routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }
        expected = {
            ("POST", "/v1/resources/applications"),
            ("POST", "/v1/resources/functions"),
            ("GET", "/v1/tower/workspaces/{workspace}/functions"),
            ("DELETE", "/v1/tower/workspaces/{workspace}/functions/{function_name}"),
            ("GET", "/v1/resources"),
            ("GET", "/v1/resources/{resource_id}"),
            ("GET", "/v1/resources/{resource_id}/operations"),
            ("GET", "/v1/resources/{resource_id}/events"),
            ("DELETE", "/v1/resources/{resource_id}"),
        }
        self.assertTrue(expected.issubset(routes), f"Missing routes: {expected - routes}")


if __name__ == "__main__":
    unittest.main()
