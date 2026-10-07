# Keycloak sign-in

Keycloak is optional. Local accounts continue to work at `/login/`. Use a build that includes this integration and run its database migrations (including `0030_keycloakconfiguration`) before enabling it. Existing published images do not gain this feature by changing configuration alone.

## Local user roles

The same Viewer, Operator and Administrator roles apply to local accounts. Open **Administration → Users & access → Manage users**, add or edit an account, and select its **Role** under **Access**. Only administrators can manage users and roles.

Existing accounts keep their access: superusers are Administrators, staff users are Operators, and other users are Viewers. A new local account defaults to Viewer. Role changes are audited and apply on subsequent requests. **Active** can be cleared to disable an account without deleting it.

For Keycloak accounts, the role selector is read-only because roles are assigned by the identity provider. Local administrators can still disable those accounts.

## Configure Keycloak

Follow these steps in order. Console labels vary between Keycloak versions; the
examples use the modern Admin Console and the client-specific dedicated scope.

### 1. Select the realm and create the client

Use the realm containing the users who will sign in. In this example:

| Setting | Example |
|---|---|
| Realm | `security` |
| Realm issuer | `https://identity.example.com/realms/security` |
| Client ID | `nsx-security-analyzer` |
| Application URL | `https://analyzer.example.com` |
| Valid redirect URI | `https://analyzer.example.com/login/keycloak/callback/` |

Replace the example hostnames. The issuer is the realm URL, not the Keycloak admin
console, discovery URL, or `/protocol/openid-connect/auth` endpoint.

Open **Clients → Create client** and select **OpenID Connect**. Configure:

| Client setting | Value required by this application |
|---|---|
| Client authentication | On |
| Standard flow | On |
| Implicit flow | Off |
| Direct access grants | Off |
| PKCE method | `S256` (may be under Advanced settings) |
| ID token signature algorithm | `RS256` |
| Valid redirect URIs | Exact callback URL above, including its trailing slash |

Copy the client secret from **Credentials** for the application GUI. Service-account
roles are not used for interactive user login. A local Docker test can use
`http://localhost:8000/login/keycloak/callback/`; the Keycloak issuer still requires HTTPS.

### 2. Create the application realm roles

Open **Realm roles → Create role** in the selected realm and create each name below.
Do not create these under **Clients → Roles**: those are client roles, stored under
a different token claim.

| Realm role name | Application GUI field | Application access |
|---|---|---|
| `nsx-analyzer-viewer` | Viewer realm role | Read application data across environments |
| `nsx-analyzer-operator` | Operator realm role | Viewer access plus collection and environment operations |
| `nsx-analyzer-admin` | Administrator realm role | Full application administration |

Names are case-sensitive. A display description, group name, or Keycloak administrator
role is not a substitute. Custom names work if the corresponding GUI field matches
exactly. If multiple mapped roles are present, the application chooses Administrator,
then Operator, then Viewer. No matching role means login is denied; there is no
implicit Viewer fallback.

### 3. Assign a role to the actual login user

For the first test, assign Viewer directly:

1. Open **Users → select the user → Role mapping → Assign role**.
2. Choose the realm-role filter (often **Filter by realm roles**).
3. Select `nsx-analyzer-viewer` and confirm **Assign**.
4. Check the user's effective role mappings.

For group-based access, assign the application realm role under **Groups → select
group → Role mapping**, then ensure the user belongs to that group. Merely naming
a group `nsx-analyzer-viewer` does not assign the realm role. For federated users,
verify effective roles in Keycloak rather than assuming an external directory group
has been mapped. Role assignment and client scope filtering are separate controls.
See [Keycloak role mappings](https://www.keycloak.org/docs/latest/server_admin/index.html#_role_scope_mappings).

### 4. Allow the application roles in the client's scope

Open **Clients → nsx-security-analyzer → Client scopes →
nsx-security-analyzer-dedicated → Scope**.

With **Full scope allowed** off, use **Assign role** to allow the three application
realm roles from step 2. This allows the client to receive those roles; it does not
grant them to users. The generated roles must satisfy both user assignment and
client scope restrictions. If Full scope allowed is already on, explicit additions
are not required for that filtering step, but assignment and the ID-token mapper
are still necessary. Do not enable broad role exposure just to bypass diagnosis.

The dedicated scope applies specifically to this client. If using a shared scope
instead, attach it as **Default**, not only **Optional**: this application requests
`openid profile email` and does not request a custom optional role scope.
See [Keycloak client scopes](https://github.com/keycloak/keycloak/blob/main/docs/documentation/server_admin/topics/clients/con-client-scopes.adoc).

### 5. Include realm roles in the ID token

In **nsx-security-analyzer-dedicated → Mappers**, select **Configure a new mapper**
(or **Add mapper → By configuration**) and choose **User Realm Role**.

| Mapper field | Value |
|---|---|
| Name | `analyzer-realm-roles` |
| Mapper type | `User Realm Role` |
| Realm Role prefix | Leave empty |
| Multivalued | On |
| Token Claim Name | `realm_access.roles` |
| Claim JSON Type | `String` |
| Add to ID token | On |
| Add to access token / userinfo | Not required by this application |

Save. If an equivalent ID-token mapper already exists, correct it instead of adding
another conflicting mapper for the same claim. A prefix changes the emitted role
names and can cause an exact-name mismatch. Avoid editing a realm-wide shared mapper
unless the change is intended for every client using it.

Keycloak commonly includes roles in the access token by default. This application
reads **only the validated ID token's `realm_access.roles`**. Roles in an access
token, userinfo response or `resource_access.<client>.roles` do not satisfy that check.
See [Keycloak token role mappings](https://github.com/keycloak/keycloak/blob/main/docs/documentation/server_admin/topics/clients/oidc/con-token-role-mappings.adoc).

### 6. Evaluate the ID token before testing login

1. Open **Clients → nsx-security-analyzer → Client scopes → Evaluate**.
2. Select the exact user who will sign in.
3. Use scope parameters `openid profile email`. Do not add optional scopes that
   the application does not request.
4. Open **Generated ID token** and inspect `realm_access.roles`.

For a Viewer using the default mapping, the relevant fragment should resemble:

```json
{
  "realm_access": {
    "roles": ["nsx-analyzer-viewer"]
  }
}
```

Other roles may also appear. At least one must exactly match a saved application
role mapping. Inspect the generated token locally; never share the complete token.

| What you see | Check next |
|---|---|
| No `realm_access.roles` | Mapper type, claim name, Add to ID token, and whether its scope applies |
| Empty array or only unrelated roles | User assignment and client's effective role scope mappings |
| Role only under `resource_access` | It was configured as a client role; create and assign a realm role |
| Role name has a prefix or different case | Mapper prefix and exact names in the application GUI |
| Correct role only in Generated access token | Enable Add to ID token on the realm-role mapper |
| Evaluation works, live login fails | Use the same realm, client, user and requested scopes; start a fresh login and verify the saved GUI mappings |

Proceed to the GUI setup below. After saving, start a new login from the application
login page. A successful **Test connection** checks TLS and discovery, not role
assignment, so it cannot replace ID-token evaluation and a complete login test.

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

### PermissionDenied after certificate trust succeeds

Older builds recorded only `PermissionDenied`; that does not identify the failed
check. New builds add a safe `reason` and `stage` to `auth.keycloak.failed` and display
an actionable login error. Provider exception bodies, tokens, authorization codes,
client secrets and full claims are never included.

| Reason | Action |
|---|---|
| `missing_roles` | Add a **User Realm Role** mapper with claim `realm_access.roles`, multivalued enabled, and **Add to ID token** enabled. Roles only in the access token are insufficient. |
| `invalid_roles` | Ensure `realm_access` is an object and its `roles` field is a list of strings, not a single string or another structure. |
| `unmapped_roles` | Assign one of the configured application realm roles to the user/group, permit it in the client's role scope, and match its name exactly in Administration → Keycloak integration. |
| `account_disabled` | A local administrator must review and reactivate the application account if appropriate. |
| `missing_state` | Start from the application's login page in the same browser and hostname. Check HTTPS cookie/proxy settings and shared database/secret-key configuration across web replicas. Do not reuse a callback URL. |
| `expired_state` | Start a new sign-in; the login attempt has a ten-minute lifetime. |
| `changed_configuration` | Restart login after saving integration settings or changing legacy deployment settings. |
| `invalid_subject` / `invalid_identity` | Check the ID-token configuration and start a new login. Subject and nonce validation remain mandatory. |
| `provider_error` | Use the exception type and stage to narrow down TLS, client authentication or token validation. Check Keycloak server events without sharing tokens or secrets. |

For the default Viewer role, the validated **ID token** should contain a structure
like this (illustration only, not a token to submit):

```json
{"realm_access": {"roles": ["nsx-analyzer-viewer"]}}
```

Keycloak **client roles** under `resource_access` do not satisfy this application's
realm-role mapping. Configure realm roles as described above. Keycloak's built-in
`realm-admin` role also does not grant access unless it is explicitly mapped; prefer
a dedicated application role. After changing roles or mappers, start a fresh login
so Keycloak issues a new ID token. Use the client's token evaluation tools to inspect
the claim locally; do not paste a complete token into tickets or application logs.
