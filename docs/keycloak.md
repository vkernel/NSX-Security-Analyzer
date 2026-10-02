# Keycloak sign-in

Keycloak is optional. Local accounts continue to work at `/login/`. Use a build that includes this integration and run its database migrations (including `0025_keycloakidentity`) before enabling it. Existing published images do not gain this feature by changing configuration alone.

## Local user roles

The same Viewer, Operator and Administrator roles apply to local accounts. Open **Administration → Users & access → Manage users**, add or edit an account, and select its **Role** under **Access**. Only administrators can manage users and roles.

Existing accounts keep their access: superusers are Administrators, staff users are Operators, and other users are Viewers. A new local account defaults to Viewer. Role changes are audited and apply on subsequent requests. **Active** can be cleared to disable an account without deleting it.

For Keycloak accounts, the role selector is read-only because roles are assigned by the identity provider. Local administrators can still disable those accounts.

## Configure Keycloak

1. Create an **OpenID Connect** client, for example `nsx-security-analyzer`.
2. Enable **Client authentication** and **Standard flow**. Disable implicit flow and direct access grants. Require PKCE method **S256**. Use **RS256** for ID token signatures.
3. Set the valid redirect URI to exactly `https://analyzer.example.com/login/keycloak/callback/`. Avoid wildcard redirect URIs. For local testing the application callback may use `http://localhost:8000/login/keycloak/callback/`; the Keycloak issuer must still use HTTPS.
4. Create these **realm roles**, and assign the appropriate role to users or groups:

   | Role | Access |
   |---|---|
   | `nsx-analyzer-viewer` | Read application data across environments |
   | `nsx-analyzer-operator` | Viewer access plus collection and environment operations |
   | `nsx-analyzer-admin` | Full application administration |

5. In the client's dedicated scope, add a **User Realm Role** protocol mapper. Set token claim name to `realm_access.roles`, enable multivalued and **Add to ID token**. Ensure the client's role scope permits the three roles above. Roles in the access token alone are insufficient.
6. Copy the client secret and realm issuer, for example `https://identity.example.com/realms/security`.

## Docker Compose

Set these values in the `.env` used by `deploy/compose.yaml` or `webapp/compose.yaml`:

```dotenv
NSX_KEYCLOAK_ENABLED=1
NSX_KEYCLOAK_ISSUER=https://identity.example.com/realms/security
NSX_KEYCLOAK_CLIENT_ID=nsx-security-analyzer
NSX_KEYCLOAK_CLIENT_SECRET=replace-with-your-client-secret
```

Recreate the web service with `docker compose up -d web`. The login page now offers **Sign in with Keycloak** using the existing application styling. These settings only need to reach the web service, not the collection worker.

## Kubernetes

Create a Secret named `nsx-keycloak` in the application namespace with a key named `client-secret`. The web Deployment explicitly maps this key to `NSX_KEYCLOAK_CLIENT_SECRET`. Do not commit the secret value to Git; use your cluster's secret management process.

Add to `nsx-config` ConfigMap `data`:

```yaml
NSX_KEYCLOAK_ENABLED: "1"
NSX_KEYCLOAK_ISSUER: "https://identity.example.com/realms/security"
NSX_KEYCLOAK_CLIENT_ID: "nsx-security-analyzer"
```

Apply or sync the configuration, then restart the web Deployment. A ConfigMap change alone does not update environment variables in existing pods. All web replicas must have the same configuration, database and Django secret key.

For HTTPS behind a trusted ingress, configure `DJANGO_HTTPS=1`, `DJANGO_TRUST_PROXY=1`, the public hostname in `DJANGO_ALLOWED_HOSTS` and public HTTPS origin in `DJANGO_CSRF_TRUSTED_ORIGINS`. Only enable proxy trust when the ingress controls the forwarded protocol header.

## Trust and connectivity

The browser must reach Keycloak, and the web container must resolve and reach Keycloak over HTTPS for token and signing-key requests. Requests time out after 15 seconds. Certificate verification is always enabled. For a private CA, mount its PEM bundle read-only into the web container and set `NSX_KEYCLOAK_CA_BUNDLE` to that path. No automatic certificate trust is performed.

## Account lifecycle and troubleshooting

The first successful sign-in creates an external user with an unusable local password. Identity is matched by realm issuer and subject, never by email or preferred username. Local accounts with the same email remain separate. Changing issuer creates a distinct identity.

At every Keycloak sign-in, roles are refreshed, including removal of administrator/operator access. No assigned application role means access is denied. A locally disabled external account stays disabled. External sessions expire after one hour; role changes and Keycloak account disablement are not pushed to already active application sessions. Disable the application user when immediate denial is required. Administrators should manage external roles in Keycloak; do not set local passwords or additional local permissions on external accounts.

Sign out ends the application session only. It does not sign out of Keycloak or other applications; a subsequent Keycloak sign-in may reuse the identity-provider session. Tokens are not stored in the database or session. State, PKCE verifier and nonce are temporary session data used during sign-in.

Failed sign-ins produce an `auth.keycloak.failed` audit event with the exception type and request ID, without tokens, authorization codes or client secrets. Check the Keycloak server events for provider details. Verify redirect URI, realm URL, client secret, TLS trust and the ID-token role mapper. Keep a working local administrator account for recovery.

Reference: [Keycloak OIDC endpoints](https://www.keycloak.org/securing-apps/oidc-layers).
