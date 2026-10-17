# Security

## Sign-in and access are managed in SPA

Harbor keeps no passwords, password policy, passkeys or directory settings. People sign in through
**SPA — Single Point of Authorization** ([repository](https://onedev.evcsbrw.ru/Monitoring/Keystone), [protocol](https://onedev.evcsbrw.ru/Monitoring/Keystone/~files/main/docs/protocol.md)): passwords, passkeys, MFA,
LDAP / Active Directory, Entra ID and other providers live there, and SPA administrators decide who gets which role in
Harbor.

- Sign-in: OpenID Connect authorization code flow with PKCE (S256), nonce and a signed single-use state; the verifier
  is kept in an HttpOnly `SameSite=Lax` cookie on `/api/auth/spa/` for 10 minutes. The ID token is verified with SPA's
  JWKS (RS256, `iss`, `aud`, `exp`, `nonce`). After the callback Harbor starts its own session.
- Users are records synchronised from SPA every 5 minutes (on the leader) and on **Synchronize now**: created, updated
  (name, e-mail, groups, role) and disabled. A person SPA disables or no longer lists loses all sessions and API tokens
  at once. Records from before SPA are bound to SPA once by username at the first sync or sign-in (then by SPA ID
  only); the old local accounts SPA does not list are disabled by the first sync.
- Sign-out ends the local session and SPA's session. "Account and security" in the user menu opens the SPA account page.
- How to connect: see [[Configuration]] (the key is pasted in Settings → SPA).

## Roles

The catalogue is fixed and published to SPA at start (`PUT /api/spa/v1/roles`):

| Role (ID) | Permissions |
|---|---|
| Administrator (`admin`) | Everything: server, routing, devices, API tokens, audit, backups, settings |
| Operator (`operator`) | VPN clients: create, edit, block, hand out configs, reissue, delete; devices and alerts |
| Viewer (`viewer`) | Read-only access to monitoring, analytics and flows |

When SPA gives several roles, the strongest one applies. People without a role cannot sign in.

## Sessions

- Sessions are random tokens in an httpOnly `SameSite=Strict` cookie; only the SHA-256 is stored in the database
  (12 h by default, `SESSION_TTL_HOURS`). Sessions can be listed and revoked in the profile.
- CSRF — state-changing requests require the `X-Requested-With: network-control` header.

## API tokens

Issued by administrators ("Users and access" page) with a role and an expiry. Format `ac_…`, shown once, only the hash
is stored. Used as `Authorization: Bearer ac_…`; they do not grant access to the profile or sessions. A token belongs
to the person who issued it: it never has more rights than their current role and is revoked when SPA disables them.

## Secrets

| Secret | Where it is stored |
|---|---|
| Client private keys | OpenBao `amnezia/clients/<id>` |
| SSH key for the VPS and MikroTik | OpenBao `amnezia/infra/ssh` (the backend writes it only to a temporary tmpfs) |
| Keystone connection key (optional) | OpenBao `amnezia/spa/connection_key` (`value`) |
| Local passwords (argon2id) and TOTP secrets | OpenBao `amnezia/local/users/<id>/password`, `…/totp` — never in PostgreSQL |
| Telegram bot token | OpenBao `amnezia/notify/telegram` |
| OPNsense API key and secret | OpenBao `amnezia/opnsense/api` |
| Database and ClickHouse passwords | OpenBao `amnezia/infra/postgres`, `amnezia/infra/clickhouse`; for the bundled services also `.env` (mode 600) |

The backend authenticates to OpenBao with an AppRole whose policy is limited to `amnezia/*` and reading the raft snapshot.
OpenBao, PostgreSQL and ClickHouse do not publish any ports.

**Static auto-unseal**: OpenBao is unsealed automatically with `openbao/keys/unseal.key`. This is convenient for
unattended operation, but the key lives on the same host: protect the host and keep a copy of the key elsewhere.

## Audit

The log records sign-ins and sign-outs, SPA synchronisations (users created, bound, updated, disabled), every client operation (including each config disclosure and share-link open),
changes to the server config, site list, custom prefixes, settings, tokens, and backups.
Filters by action type and search; entries appear in real time.

## Recommendations

- Expose the panel only on the LAN or through a VPN; for external access use a reverse proxy with HTTPS and `COOKIE_SECURE=true`.
- Require MFA or passkeys for administrators in SPA.
- Use dedicated SSH keys and a restricted user on MikroTik.
