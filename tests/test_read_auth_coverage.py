import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import example, info, retrieval


class ReadAuthCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(example.router, prefix="/examples")
        app.include_router(info.router, prefix="/model")
        app.include_router(retrieval.router, prefix="/retrieval")
        self.client = TestClient(app)

    def test_read_routes_require_operator_identity(self) -> None:
        self.assertEqual(self.client.get("/examples/categories").status_code, 401)
        self.assertEqual(self.client.get("/model/info").status_code, 401)
        self.assertEqual(self.client.get("/retrieval/config").status_code, 401)

    def test_example_read_allows_knowledge_operator(self) -> None:
        response = self.client.get(
            "/examples/categories",
            headers={"X-User-Role": "knowledge_ops", "X-Operator-Id": "ops_1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("categories", response.json())


if __name__ == "__main__":
    unittest.main()
