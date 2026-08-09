# Secret handling

## Never store in Git or OpenClaw memory

- AdsPower profile cookies
- Seller Central passwords
- Email passwords
- API keys
- OAuth tokens
- Session storage dumps
- `.env` files
- Real `config/accounts.json`

## Local-only locations

```text
runtime/private/accounts.json
.env
```

## Environment variables

```powershell
$env:AMAZON5461_ACCOUNTS_PATH="runtime/private/accounts.json"
$env:ADSPOWER_API_BASE_URL="http://local.adspower.net:50325"
```

## Logging rule

Logs may include account IDs and brand names, but must not include passwords, cookies, tokens, full profile fingerprints, or email credentials.
