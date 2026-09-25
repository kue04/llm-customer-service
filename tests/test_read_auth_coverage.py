import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_helpers import auth_headers
from routers import example, info, retrieval


class ReadAuthCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(example.router, prefix="/examples")
        app.include_router(info.router, prefix="/model")
        app.include_router(retrieval.router, prefix="/retrieval")
        self.client = TestClient(app)

    def test_read_routes_require_access_token(self) -> None:
        self.assertEqual(self.client.get("/examples/categories").status_code, 401)
        self.assertEqual(self.client.get("/model/info").status_code, 401)
        self.assertEqual(self.client.get("/retrieval/config").status_code, 401)

    def test_legacy_identity_headers_do_not_authenticate(self) -> None:
        """只发旧信任头、不发 JWT —— 必须 401。

        兼容期这些头只记日志，绝不能换来任何身份。
        """

        for path in ("/examples/categories", "/model/info", "/retrieval/config"):
            response = self.client.get(
                path,
                headers={"X-User-Role": "admin", "X-Operator-Id": "admin_1"},
            )
            self.assertEqual(response.status_code, 401, path)

    def test_example_read_allows_knowledge_operator(self) -> None:
        response = self.client.get(
            "/examples/categories",
            headers=auth_headers(roles=["knowledge_ops"], user_id="ops_1"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("categories", response.json())


if __name__ == "__main__":
    unittest.main()
