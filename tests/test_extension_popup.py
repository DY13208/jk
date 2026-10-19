"""Popup/worker host-permission contract (static checks; the popup has no build step)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'chrome-extension'


def test_permission_request_is_synchronous_in_click_and_uses_worker_pattern():
    popup = (ROOT / 'popup.js').read_text(encoding='utf-8')
    worker = (ROOT / 'worker.js').read_text(encoding='utf-8')
    handler = re.search(r"\$\('#grant'\)\.onclick = \(\) => \{(.*?)\n\};", popup, re.S).group(1)
    # No await/async before permissions.request: the user gesture must still be active.
    assert 'await' not in handler.split('chrome.permissions.request')[0] and 'async' not in handler.split('chrome.permissions.request')[0]
    # Popup requests "<origin>/*" and the worker checks exactly the same pattern.
    assert "new URL(s.url).origin + '/*'" in popup
    assert "chrome.permissions.contains({origins: [origin + '/*']})" in worker


def test_popup_keeps_all_actions():
    html = (ROOT / 'popup.html').read_text(encoding='utf-8')
    popup = (ROOT / 'popup.js').read_text(encoding='utf-8')
    for element in ('id="verify"', 'id="grant"', 'id="poll"', 'id="cleanup"', 'id="site"', 'http://127.0.0.1:8080/'):
        assert element in html
    for kind in ("kind: 'poll'", "kind: 'cleanup'", "kind: 'verify'", "kind: 'sites'"):
        assert kind in popup
    assert 'https://' not in re.sub(r'http://127\.0\.0\.1:8080/|http://www\.w3\.org/2000/svg', '', html)  # no CDN assets
