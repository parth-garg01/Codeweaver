"""The UI serves the local filesystem, so its boundary is a security boundary."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from codeweaver.ui.api import ApiError, ApiRouter
from codeweaver.ui.server import STATIC_DIR, create_server

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CLEAN = FIXTURES / "sample_project"


class ServerBindingTests(unittest.TestCase):
    def test_refuses_a_routable_bind_address(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            create_server(host="0.0.0.0", port=0)  # noqa: S104 - asserting refusal
        self.assertIn("loopback", str(ctx.exception))

    def test_accepts_loopback(self) -> None:
        server = create_server(host="127.0.0.1", port=0, quiet=True)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()


class LiveServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(host="127.0.0.1", port=0, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str, host: str | None = None):
        request = urllib.request.Request(self.url(path))
        if host is not None:
            request.add_header("Host", host)
        return urllib.request.urlopen(request, timeout=10)  # noqa: S310 - fixed localhost URL

    def post(self, path: str, payload: dict, raw: bytes | None = None):
        data = raw if raw is not None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url(path), data=data, headers={"Content-Type": "application/json"}
        )
        return urllib.request.urlopen(request, timeout=30)  # noqa: S310 - fixed localhost URL


class StaticServingTests(LiveServerTestCase):
    def test_root_serves_the_page(self) -> None:
        response = self.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.headers["Content-Type"])

    def test_responses_set_nosniff_and_a_content_security_policy(self) -> None:
        response = self.get("/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_no_cors_header_is_sent(self) -> None:
        self.assertIsNone(self.get("/").headers.get("Access-Control-Allow-Origin"))

    def test_path_traversal_is_refused(self) -> None:
        for path in ("/../pyproject.toml", "/..%2f..%2fpyproject.toml", "/static/../../x"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404, path)

    def test_a_missing_asset_is_a_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope.js")
        self.assertEqual(ctx.exception.code, 404)

    def test_a_non_local_host_header_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/", host="evil.example.com")
        self.assertEqual(ctx.exception.code, 421)
        self.assertIn("localhost", ctx.exception.read().decode("utf-8"))


class ApiOverHttpTests(LiveServerTestCase):
    def test_languages_endpoint(self) -> None:
        payload = json.loads(self.get("/api/languages").read())
        self.assertEqual(payload["matrix"]["pairs"][0]["source"], "python")

    def test_execution_ceiling_is_reported(self) -> None:
        payload = json.loads(self.get("/api/languages").read())
        self.assertFalse(payload["execution"]["allowTests"])

    def test_scan_endpoint(self) -> None:
        payload = json.loads(self.post("/api/scan", {"project": str(CLEAN)}).read())
        self.assertEqual(payload["languageCounts"], {"python": 5})

    def test_unknown_endpoint_is_a_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_a_get_on_a_post_endpoint_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/scan")
        self.assertEqual(ctx.exception.code, 404)

    def test_a_non_json_body_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/scan", {}, raw=b"not json")
        self.assertEqual(ctx.exception.code, 400)

    def test_a_json_array_body_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/scan", {}, raw=b"[]")
        self.assertEqual(ctx.exception.code, 400)

    def test_a_missing_project_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/scan", {})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("project is required", ctx.exception.read().decode("utf-8"))

    def test_a_non_existent_project_is_refused(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/scan", {"project": "/no/such/project"})
        self.assertEqual(ctx.exception.code, 400)


class FilePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ApiRouter()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "out"
        (self.root / "pkg").mkdir(parents=True)
        (self.root / "pkg" / "a.ts").write_text("const a = 1;\n", encoding="utf-8")
        (self.base / "secret.txt").write_text("TOKEN", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def preview(self, path: str):
        return self.router.handle("GET", "/api/file", {"root": str(self.root), "path": path}, {})

    def test_reads_a_file_inside_the_root(self) -> None:
        self.assertEqual(self.preview("pkg/a.ts")["contents"], "const a = 1;\n")

    def test_dot_dot_traversal_is_refused(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.preview("../secret.txt")
        self.assertIn("escapes project root", str(ctx.exception))

    def test_an_absolute_path_outside_the_root_is_refused(self) -> None:
        with self.assertRaises(ApiError):
            self.preview(str(self.base / "secret.txt"))

    def test_a_symlink_out_of_the_root_is_refused(self) -> None:
        (self.root / "link.txt").symlink_to(self.base / "secret.txt")
        with self.assertRaises(ApiError):
            self.preview("link.txt")

    def test_a_missing_file_is_a_404(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.preview("pkg/missing.ts")
        self.assertEqual(ctx.exception.status, 404)

    def test_a_directory_is_not_a_file(self) -> None:
        with self.assertRaises(ApiError):
            self.preview("pkg")

    def test_an_oversized_file_is_refused(self) -> None:
        from codeweaver.ui.api import MAX_PREVIEW_BYTES

        big = self.root / "big.ts"
        big.write_text("x" * (MAX_PREVIEW_BYTES + 1), encoding="utf-8")
        with self.assertRaises(ApiError) as ctx:
            self.preview("big.ts")
        self.assertIn("too large", str(ctx.exception))

    def test_a_binary_file_is_refused(self) -> None:
        (self.root / "blob.bin").write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(ApiError) as ctx:
            self.preview("blob.bin")
        self.assertIn("not UTF-8", str(ctx.exception))

    def test_missing_parameters_are_refused(self) -> None:
        with self.assertRaises(ApiError):
            self.router.handle("GET", "/api/file", {}, {})


class ExecutionCeilingTests(unittest.TestCase):
    def test_a_request_cannot_widen_the_server_policy(self) -> None:
        router = ApiRouter(allow_tests=False, allow_install=False)
        policy = router._policy({"allowTests": True, "allowInstall": True})
        self.assertFalse(policy.allow_tests)
        self.assertFalse(policy.allow_install)

    def test_a_request_can_narrow_the_server_policy(self) -> None:
        router = ApiRouter(allow_tests=True, allow_install=True)
        policy = router._policy({"allowTests": False, "allowInstall": False})
        self.assertFalse(policy.allow_tests)

    def test_an_enabled_server_honours_an_enabling_request(self) -> None:
        router = ApiRouter(allow_tests=True)
        self.assertTrue(router._policy({"allowTests": True}).allow_tests)

    def test_typechecking_is_on_by_default(self) -> None:
        self.assertTrue(ApiRouter()._policy({}).allow_typecheck)


class StaticAssetTests(unittest.TestCase):
    def test_the_packaged_directory_exists_with_an_index(self) -> None:
        self.assertTrue(STATIC_DIR.is_dir())
        self.assertTrue((STATIC_DIR / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
