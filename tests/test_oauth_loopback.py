import threading
import urllib.request

from brain import oauth_loopback


def test_loopback_listener_captures_callback():
    port = 8765
    path = "/api/integrations/test/callback"

    with oauth_loopback.start(port, path) as listener:
        def _hit_callback():
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}?code=abc123&state=xyz", timeout=5)

        thread = threading.Thread(target=_hit_callback)
        thread.start()
        result = listener.wait(timeout_seconds=5)
        thread.join()

    assert result.code == "abc123"
    assert result.state == "xyz"
    assert result.error is None


def test_loopback_listener_captures_error():
    port = 8766
    path = "/api/integrations/test/callback"

    with oauth_loopback.start(port, path) as listener:
        def _hit_callback():
            urllib.request.urlopen(f"http://127.0.0.1:{port}{path}?error=access_denied&state=xyz", timeout=5)

        thread = threading.Thread(target=_hit_callback)
        thread.start()
        result = listener.wait(timeout_seconds=5)
        thread.join()

    assert result.error == "access_denied"
