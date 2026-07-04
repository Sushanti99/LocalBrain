# BrainSquared Roadmap

## Now (Sign-in identity)

- [x] Supabase Auth wired into Mac app (Sign in with Google)
- [x] User identity tied to Supabase `auth.users` (email captured)
- [x] Sign-in step blocks onboarding until complete
- [ ] Settings → Account section (sign out, delete account)
- [ ] Switch session storage from `UserDefaultsAuthStorage` back to `KeychainLocalStorage` once we have a stable Apple Developer signing identity (current dev workaround avoids keychain ACL prompts that fire on every rebuild)

## Next (Cloud-backed integrations)

The Mac app currently completes each integration's OAuth flow locally and stores tokens on disk. Move that to a Composio/Supermemory-style model where Supabase holds the canonical token store per-user, and the Mac app reads/writes via authenticated API calls.

### Architecture target

```
Mac app  ──(Supabase JWT)──▶  Supabase Edge Functions / Postgres
                                  │
                                  ├── /oauth/<provider>/start   (returns auth URL)
                                  ├── /oauth/<provider>/callback (Supabase HTTPS — receives token)
                                  └── /integrations/<provider>/data (proxies provider API)
```

### Why this matters

- Standard HTTPS callback URL — no custom URL schemes, no localhost port hacks
- Tokens refresh server-side (provider tokens expire — central refresh is much simpler)
- Users sign in on a new machine and all their connections are already there
- Foundation for web/mobile clients later

### Migration path

1. Add `integration_tokens` table in Supabase (per-user, per-provider, with refresh_token, scopes, expires_at)
2. Move OAuth callback handling from local FastAPI to Supabase Edge Functions
3. Mac app trades direct provider API calls for calls to a thin BrainSquared API
4. Local token files become a fallback / cache, not the source of truth

### Open questions

- Provider-side: register one OAuth app per provider for BrainSquared (Google, Linear done via `brain connect`/web UI; GitHub/Slack/Notion pending)
- Where data fetching runs — server-side (privacy concern: data passes through our backend) vs. client-side using server-issued tokens (riskier but private)
- How to handle the existing local-token users on upgrade (migration: prompt re-auth on first launch after update)

## Later

- iOS/iPadOS client (same Supabase backend)
- Web client at app.brainsquared.so
- Team/org accounts (shared vault, shared integrations)
