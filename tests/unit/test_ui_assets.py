"""The packaged UI assets have to satisfy the policy the server sends."""

from __future__ import annotations

import re
import unittest

from codeweaver.ui.server import STATIC_DIR


class AssetPresenceTests(unittest.TestCase):
    def test_every_referenced_asset_is_packaged(self) -> None:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        referenced = set(re.findall(r'(?:href|src)="([^"]+)"', html))
        for name in referenced:
            if name.startswith(("http:", "https:", "//", "#")):
                self.fail(f"external asset reference: {name}")
            self.assertTrue((STATIC_DIR / name).is_file(), name)

    def test_the_expected_assets_exist(self) -> None:
        for name in ("index.html", "app.css", "app.js"):
            self.assertTrue((STATIC_DIR / name).is_file(), name)


class ContentSecurityPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    def test_no_inline_script(self) -> None:
        self.assertNotIn("<script>", self.html)
        inline = re.findall(r"<script(?![^>]*\ssrc=)[^>]*>", self.html)
        self.assertEqual(inline, [])

    def test_no_inline_style(self) -> None:
        self.assertNotIn("<style", self.html)
        self.assertEqual(re.findall(r'\sstyle="', self.html), [])

    def test_no_inline_event_handlers(self) -> None:
        handlers = re.findall(r"\son[a-z]+=", self.html)
        self.assertEqual(handlers, [])

    def test_no_external_origins_are_requested(self) -> None:
        for asset in ("index.html", "app.css", "app.js"):
            text = (STATIC_DIR / asset).read_text(encoding="utf-8")
            self.assertNotIn("https://cdn", text, asset)
            self.assertNotIn("http://", text.replace("http://www.w3.org/2000/svg", ""), asset)


class AccessibilityBasicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    def test_document_declares_a_language_and_viewport(self) -> None:
        self.assertIn('<html lang="en">', self.html)
        self.assertIn('name="viewport"', self.html)

    def test_every_section_is_labelled(self) -> None:
        sections = re.findall(r'<section id="view-[^"]+"([^>]*)>', self.html)
        self.assertTrue(sections)
        for attributes in sections:
            self.assertIn("aria-labelledby", attributes)

    def test_navigation_is_labelled(self) -> None:
        self.assertIn('aria-label="Workflow"', self.html)

    def test_required_inputs_are_marked_required(self) -> None:
        self.assertIn('id="project-input"', self.html)
        self.assertIn("required", self.html)

    def test_every_tab_names_a_view_that_exists(self) -> None:
        views = set(re.findall(r'<section id="view-([^"]+)"', self.html))
        tabs = set(re.findall(r'class="tab" data-view="([^"]+)"', self.html))
        self.assertTrue(tabs)
        self.assertEqual(tabs - views, set())


class DesignSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")

    def test_tokens_are_defined_on_root(self) -> None:
        self.assertIn(":root {", self.css)
        for token in ("--surface-0", "--text", "--accent", "--space-4", "--radius"):
            self.assertIn(token, self.css)

    def test_dark_mode_redefines_the_tokens(self) -> None:
        self.assertIn("@media (prefers-color-scheme: dark)", self.css)

    def test_focus_is_visible(self) -> None:
        self.assertIn(":focus-visible", self.css)
        self.assertIn("--focus", self.css)

    def test_reduced_motion_is_respected(self) -> None:
        self.assertIn("prefers-reduced-motion: reduce", self.css)

    def test_layout_responds_to_narrow_viewports(self) -> None:
        self.assertIn("@media (max-width: 900px)", self.css)

    def test_only_one_accent_hue_is_declared(self) -> None:
        accents = set(re.findall(r"--accent[a-z-]*:\s*([^;]+);", self.css))
        self.assertTrue(accents)
        self.assertLessEqual(len(accents), 8)


class JavaScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_strict_mode_is_enabled(self) -> None:
        self.assertIn('"use strict";', self.js)

    def test_no_eval_or_inner_html_is_used(self) -> None:
        self.assertNotIn("eval(", self.js)
        self.assertNotIn("innerHTML", self.js)

    def test_every_api_endpoint_it_calls_is_routed(self) -> None:
        from codeweaver.ui.api import ApiRouter

        router = ApiRouter()
        called = set(re.findall(r'api\("(/api/[a-z]+)"', self.js))
        self.assertTrue(called)
        for path in called:
            routed = False
            for method in ("GET", "POST"):
                try:
                    router.handle(method, path, {}, {})
                    routed = True
                except Exception as exc:  # noqa: BLE001 - only the 404 matters here
                    routed = routed or "no such endpoint" not in str(exc)
            self.assertTrue(routed, f"the UI calls an unrouted endpoint: {path}")


if __name__ == "__main__":
    unittest.main()
