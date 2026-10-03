import tempfile
import subprocess
import unittest
from pathlib import Path
from urllib.request import urlopen

from github_radar.browser_server import BrowserServer
from github_radar.service import RadarService
from github_radar.storage import RadarStore


class BrowserUiDeliveryTests(unittest.TestCase):
    def test_ai_controls_are_semantic(self):
        _, _, html = self.fetch("/")
        self.assertIn('id="ai-model"', html)
        self.assertIn('id="detail-ai-model"', html)
        self.assertIn('id="ai-connect"', html)
        self.assertIn('id="detail-ai-explain"', html)
        self.assertIn('id="keyword-groups"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("消耗", html)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = RadarStore(Path(self.temp.name) / "UserData")
        self.server = BrowserServer(RadarService(object(), store), store)
        self.server.start()
        self.addCleanup(self.server.close)
        self.base = self.server.url.split("/#", 1)[0]

    def fetch(self, path):
        with urlopen(self.base + path, timeout=3) as response:
            return response.status, response.headers, response.read().decode("utf-8")

    def test_home_and_direct_detail_deliver_semantic_local_shell(self):
        for path in ("/", "/project/123"):
            status, headers, html = self.fetch(path)
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn("<h1", html)
            self.assertIn("<main", html)
            self.assertIn('for="keyword-input"', html)
            self.assertIn('id="refresh-button"', html)
            self.assertIn('id="quit-button"', html)
            self.assertIn('id="home-view"', html)
            self.assertIn('id="detail-view"', html)
            self.assertIn('id="detail-status-text"', html)
            self.assertIn('src="/assets/app.js"', html)
            self.assertIn('href="/assets/app.css"', html)
            self.assertNotIn("https://", html)

    def test_history_following_and_settings_have_simple_navigation(self):
        for path in ("/history", "/following", "/settings"):
            status, _, html = self.fetch(path)
            self.assertEqual(status, 200)
            self.assertIn('id="primary-nav"', html)
            self.assertIn('href="/history"', html)
            self.assertIn('href="/following"', html)
            self.assertIn('href="/settings"', html)
            self.assertIn('id="history-view"', html)
            self.assertIn('id="following-view"', html)
            self.assertIn('id="history-date-groups"', html)
            self.assertIn('id="following-cards"', html)
            self.assertIn('id="follow-button"', html)
            self.assertIn('id="settings-keywords"', html)
            self.assertIn('id="settings-feedback"', html)

    def test_local_assets_include_accessible_motion_and_text_only_rendering(self):
        status, headers, css = self.fetch("/assets/app.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("SimSun", css)
        self.assertIn("Times New Roman", css)
        status, headers, js = self.fetch("/assets/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn("textContent", js)
        self.assertIn("sessionStorage", js)
        self.assertNotIn("innerHTML", js)
        # Official authorization/source links are allowed; runtime assets and API
        # fetches must remain local rather than loading third-party scripts.
        self.assertNotRegex(js, r"fetch\s*\(\s*['\"]https?://")

    def test_language_and_font_controls_are_delivered_locally(self):
        _, _, html = self.fetch("/settings")
        self.assertIn('id="language-toggle"', html)
        self.assertIn('id="font-size-controls"', html)
        self.assertIn('src="/assets/i18n.js"', html)
        _, _, css = self.fetch("/assets/app.css")
        self.assertIn('for="font-scale"', html)
        self.assertIn('min="0.8" max="2"', html)
        self.assertIn('var(--font-scale)', css)
        self.assertNotIn("zoom:", css)

    def test_daily_schedule_has_labeled_keyboard_form_and_live_feedback(self):
        _, _, html = self.fetch("/settings")
        self.assertIn('id="auto-update-form"', html)
        self.assertIn('for="auto-update-time"', html)
        self.assertIn('id="auto-update-enabled"', html)
        self.assertIn('id="auto-update-time-error"', html)
        self.assertIn('id="auto-update-feedback"', html)
        self.assertIn('aria-live="polite"', html)

    def test_browser_interaction_states(self):
        script = Path(__file__).with_name("browser_interaction.cjs")
        result = subprocess.run(["node", str(script)], capture_output=True, text=True,
                                encoding="utf-8",
                                timeout=10, check=False)
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))


if __name__ == "__main__":
    unittest.main()
