# Google OAuth Verification — Demo Video Recording Guide

Goal: produce the ~90-second screencast Google requires for OAuth app
verification (Gmail is a *sensitive* scope). The video must show the real
OAuth flow end-to-end: user clicks **Sign in with Google** → consent screen
(client_id + scopes legible) → authorized → the app **using the data**.

Plan: `docs/superpowers/plans/2026-08-26-gmail-oauth-connectkit.md`
Demo page: `GET /dev/gmail-demo` (`src/http/static/gmail-demo.html`)

---

## 0. Prerequisites (do these first — order matters)

1. **Google OAuth client**
   - In Google Cloud Console (`executive-assistant-auth` project), confirm an
     OAuth 2.0 Client ID exists (web-app type) with redirect URI:
     `http://localhost:8000/auth/callback` (or your API_PUBLIC_URL + `/auth/callback`)
   - Copy its **client_id** and **client_secret**

2. **Configure the app** — pick one:
   - Via API (recommended, matches the video):
     ```bash
     curl -X POST "http://localhost:8000/connectors/connect?service=gmail&user_id=default_user" \
       -H "Content-Type: application/json" \
       -d '{"client_id": "<YOUR_CLIENT_ID>", "client_secret": "<YOUR_CLIENT_SECRET>"}'
     ```
     → expect `{"status": "configured", "next_step": "Open /auth/login?service=gmail ..."}`
   - Or via env: `DEFAULT_GWS_CLIENT_ID=... DEFAULT_GWS_CLIENT_SECRET=...`

3. **Set the vault key** (tokens survive restarts — required for shot 6):
   ```bash
   export CONNECTKIT_VAULT_KEY="$(openssl rand -hex 32)"
   ```

4. **⚠️ G3 dependency — make sync actually work**
   - Currently `POST /emails/sync` still routes through the broken `gws` CLI.
   - **Until G3 lands** (wire `sync_emails` to `GmailClient`), the demo's
     *search* works off HybridDB, but the "data flows" shot won't populate.
   - Either complete G3 first, or pre-seed the HybridDB store before recording.

---

## 1. Start the server

```bash
uv run assistant http
# → "Starting assistant HTTP API on 0.0.0.0:8080" (or API_PORT)
```

Verify the catalog includes gmail:

```bash
curl -s "http://localhost:8080/connectors/catalog?user_id=default_user" | jq
# expect gmail present, "connected": false
```

Open the demo page: **http://localhost:8080/dev/gmail-demo**
→ should show "Not connected" badge + **Sign in with Google** button.

---

## 2. Recording setup (macOS)

- **QuickTime Player** → File → New Screen Recording, or **Shift+Cmd+5**
- Record the browser window **at readable zoom** — the consent screen text
  must be legible (Google rejects videos where scopes aren't readable)
- Optional: brief narration while clicking

---

## 3. Shot-by-shot script (~90 seconds)

| # | Time | What to show | Check |
|---|---|---|---|
| 1 | 0:00–0:10 | Demo page loads: **Not connected** badge + Sign in with Google button | Badge visible |
| 2 | 0:10–0:20 | Click **Sign in with Google** → Google consent screen | **client_id + `gmail.readonly` legible** — the critical shot |
| 3 | 0:20–0:35 | Choose account → **Allow** → redirect back | Page flips to **Connected** badge |
| 4 | 0:35–0:45 | Type a query in the search box, e.g. `from:client` or `invoice` | Results appear from `/emails/search` |
| 5 | 0:45–1:10 | Click **Sync now** → "sync started" → re-search | New items appear (proves token works against the Gmail API) |
| 6 | 1:10–1:30 | *(optional but strong)* Stop server, restart, reload page | Still **Connected** + search works — proves refresh token + vault key |

---

## 4. Submission

1. Upload the recording:
   - **YouTube (unlisted)** — recommended, or
   - **Google Drive** (anyone-with-link)
2. Paste the **link** into the OAuth verification form (a file upload is not
   accepted)
3. Confirm the scopes shown in the video match the verification request
   (should be `gmail.readonly`)

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Consent screen shows empty client_id / broken URL | Connector not configured | Run step 0.2 (connect or env) |
| `401` / error after Allow | Wrong redirect URI in console, or stale token | Verify redirect URI matches `API_PUBLIC_URL`/localhost; reconnect |
| "Not connected" after restart | `CONNECTKIT_VAULT_KEY` unset | Set it (step 0.3); without it tokens are lost on restart |
| gmail missing from catalog | Spec dir not found | Dev defaults to `packages/connectkit/connectors` automatically; production sets `CONNECTKIT_SPEC_DIR` |
| Sync button fails / no new mail | gws path not yet replaced | Complete G3, or pre-seed HybridDB |
| Consent screen scope differs from request | Wrong OAuth client | Record with the same client_id as the verification form |

---

## 6. After submission

- Keep the app in **Testing** mode until verification completes (100-user
  limit while pending)
- The video link can be reused for future scope additions (record a new one
  only if you add scopes like `gmail.send` for Phase 2 draft-delivery)
