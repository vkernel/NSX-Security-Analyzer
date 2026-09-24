# Security

## Reporting a vulnerability

Do not post credentials, exploit details or sensitive inventory in public issues.
Use the repository's **Security → Report a vulnerability** feature if available.
If private reporting is unavailable, open a minimal issue requesting a private
contact channel without describing the vulnerability or attaching sensitive data.

Include affected revision, deployment mode, impact and a minimal sanitized
reproduction through the private channel. There is no published response SLA or
supported-version matrix yet.

## Deployment boundaries

- All signed-in users can read all environments. Staff can manage environments and collections; superusers manage accounts and permissions.
- Use an NSX account with the required read permissions. Collection uses GET requests.
- Keep TLS verification enabled and upload trusted CA certificates as needed.
- Protect `.env`, database backups and `DJANGO_SECRET_KEY`. Saved manager passwords use authenticated encryption derived from this secret.
- The database contains sensitive inventory, uploaded CA certificates, configuration and encrypted credentials. Limit access and encrypt backups at rest.
- Put shared deployments behind HTTPS and apply login rate limiting at the proxy. Trust forwarded headers only from a proxy you control.
- Do not commit real reports, database dumps, private keys or local credential files.
- Demo data is synthetic and collection is disabled for newly created demo environments.

See [operations](docs/operations.md) for backup and recovery considerations.
