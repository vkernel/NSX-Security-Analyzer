# Keycloak sign-in

Keycloak is optional. Local accounts continue to work at `/login/`. Use a build that includes this integration and run its database migrations (including `0030_keycloakconfiguration`) before enabling it. Existing published images do not gain this feature by changing configuration alone.

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

## Configure the application in the web GUI

The same workflow applies to Docker Compose and Kubernetes. No Keycloak variables,
client-secret Kubernetes Secret, or certificate volume mount is required for a new
GUI-managed integration.

1. Sign in with a local administrator and open **Administration → Keycloak integration**.
2. Enter the full **Realm issuer URL**, **Client ID**, and **Client secret** from Keycloak.
3. Confirm the Viewer, Operator and Administrator realm role names. Defaults match
   the table above; use distinct names and include these roles in the ID token.
4. Copy the exact callback URL shown on the page into Keycloak's valid redirect URIs.
   Open the application through its public HTTPS URL so the displayed callback is correct.
5. For a private or self-signed certificate, click **Retrieve certificate**. The
   submitted client-secret draft stays in the masked field and is not saved to the session.
6. Verify the displayed SHA-256 fingerprint with your identity administrator, then
   select **I verified the fingerprint and trust this certificate**. Retrieval alone
   does not trust it. The preview expires after 10 minutes and is bound to the issuer.
7. Click **Test connection**. This verifies TLS and realm discovery without sending
   the client secret or saving changes. It does not validate client credentials or roles.
8. Select **Enable Keycloak sign-in** and **Save integration**. Enabling also performs
   the connection check. Try **Sign in with Keycloak** in a separate browser session
   while retaining your local administrator session for recovery.

Settings are stored in PostgreSQL and read on each authentication request. They apply
across web replicas without a pod restart. The client secret is encrypted using a key
derived from `DJANGO_SECRET_KEY`; keep this key stable, shared across replicas and
backed up separately with your recovery material. Saved secrets are never redisplayed.
Leave the secret field blank to retain the existing value. Changing the issuer requires
new credentials when enabling and removes trust associated with the previous issuer.
Changes are audited without logging the secret or tokens. Pending login attempts
must restart after configuration changes.

To disable SSO, clear **Enable Keycloak sign-in** and save. This does not require a
successful connection test. Local login remains available. It does not revoke already
active user sessions; disable an application user when immediate denial is needed.

## HTTPS, certificate trust and connectivity

For HTTPS behind a trusted ingress or Gateway, configure `DJANGO_HTTPS=1`,
`DJANGO_TRUST_PROXY=1`, the public hostname in `DJANGO_ALLOWED_HOSTS` and public HTTPS
origin in `DJANGO_CSRF_TRUSTED_ORIGINS`. These deployment settings still belong in
the deployment configuration. Only enable proxy trust when the Gateway controls
and overwrites `X-Forwarded-Proto`.

The browser and web pods must reach Keycloak. Certificate verification, hostname checks
and certificate validity checks remain enabled. The approved certificate is stored
in the database and used in memory; no certificate files need to be mounted. Retrieval
approves the presented server certificate, not an automatically discovered CA chain.
Certificate renewal can therefore require retrieving and approving its replacement.
Use **Use the default certificate trust store** to remove custom trust when Keycloak
uses a certificate trusted by the container's default CA store.

## Existing YAML/environment configurations

Until the first GUI save, existing `NSX_KEYCLOAK_ENABLED`, `NSX_KEYCLOAK_ISSUER`,
`NSX_KEYCLOAK_CLIENT_ID`, `NSX_KEYCLOAK_CLIENT_SECRET` and `NSX_KEYCLOAK_CA_BUNDLE`
settings remain a fallback. The page shows when this fallback is in use. Saving
adopts the existing secret if the secret field is blank and the issuer is unchanged.
Afterward, saved GUI settings take precedence, including an explicitly disabled
integration. Changing environment variables no longer overrides them.

If the old setup used a CA file path, retain that mount until you retrieve and approve
the certificate in the GUI or switch to the default trust store. The first save preserves
the old path to avoid breaking trust unexpectedly. After testing GUI-managed login,
you can remove the legacy Keycloak environment entries and optional `nsx-keycloak`
Secret mapping from your deployment. Do not remove `DJANGO_SECRET_KEY`.

## Account lifecycle and troubleshooting

The first successful sign-in creates an external user with an unusable local password. Identity is matched by realm issuer and subject, never by email or preferred username. Local accounts with the same email remain separate. Changing issuer creates a distinct identity.

At every Keycloak sign-in, roles are refreshed, including removal of administrator/operator access. No assigned application role means access is denied. A locally disabled external account stays disabled. External sessions expire after one hour; role changes and Keycloak account disablement are not pushed to already active application sessions. Disable the application user when immediate denial is required. Administrators should manage external roles in Keycloak; do not set local passwords or additional local permissions on external accounts.

Sign out ends the application session only. It does not sign out of Keycloak or other applications; a subsequent Keycloak sign-in may reuse the identity-provider session. Tokens are not stored in the database or session. State, PKCE verifier and nonce are temporary session data used during sign-in.

Failed sign-ins produce an `auth.keycloak.failed` audit event with the exception type and request ID, without tokens, authorization codes or client secrets. Check the Keycloak server events for provider details. Verify redirect URI, realm URL, client secret, TLS trust and the ID-token role mapper. Keep a working local administrator account for recovery.

Reference: [Keycloak OIDC endpoints](https://www.keycloak.org/securing-apps/oidc-layers).
