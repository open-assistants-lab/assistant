"""Dev-route tests — the Gmail demo page used for the OAuth verification video."""


def test_gmail_demo_page_served(client):
    r = client.get("/dev/gmail-demo")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    # The critical affordances for the verification video:
    assert "Sign in with Google" in body  # triggers /auth/login?service=gmail
    assert "/auth/login?service=gmail" in body
    assert "/emails/search" in body  # real search surface over HybridDB
    assert "/emails/sync" in body
