# LDAPS authentication

Open **Administration → Authentication providers → LDAPS** as an administrator.
Keycloak lives on the same page under its own tab; local sign-in always remains available.
Apply migrations through `0034` using the application's migration job before opening the page.
Use a new image containing the LDAPS dependency, not an older image with only changed YAML.

## Requirements

- An LDAPS endpoint reachable from each web pod, usually TCP 636, with TLS 1.2 or later.
- A hostname matching the server certificate. Use `ldaps://directory.example.com:636`.
- A read-only service account allowed to search your user base and read the identity,
  name and `memberOf` attributes.
- Directory groups for the application roles. Users must be direct members of at least
  one configured group. Nested groups and Active Directory primary groups are not expanded.
- HTTPS for the application's public login page.

This integration supports simple bind over verified LDAPS. Plain LDAP, StartTLS,
Kerberos, NTLM, password changes and MFA challenges are not implemented.
For MFA or complex federation, use Keycloak instead. Apply login rate limits at your
trusted ingress and your directory's account lockout policy.

## Configure the provider

1. Enter the primary LDAPS URL and, optionally, a secondary URL for another replica of the same directory. Leave secondary blank for a single server. Enter the shared service-account bind DN and password. The password is encrypted
   using the application secret key. A blank password preserves the saved value; changing
   the primary server requires a new password and resets its custom certificate trust. Adding or changing a secondary server while enabled also requires re-entering the password, and resets only secondary trust.
2. Enter the user search base, for example `OU=People,DC=example,DC=com`.
3. For Active Directory use `sAMAccountName` as the username attribute and `objectGUID`
   as the immutable identity attribute. Users then sign in with their short account name.
   For OpenLDAP use `uid` and `entryUUID`; enable the directory's memberOf support and ensure
   those operational attributes can be read by the service account. `userPrincipalName`
   can also be used for AD if users should enter their UPN.
4. Enter full group DNs, for example `CN=NSX-Viewers,OU=Groups,DC=example,DC=com`.
   Configure Viewer, Operator and/or Administrator. Unconfigured mappings grant no role.
   When multiple mappings match, Administrator takes precedence over Operator, then Viewer.
5. If the endpoint uses a certificate not trusted by the container, select **Retrieve primary certificate** or **Retrieve secondary certificate** for that endpoint. Check the SHA-256 fingerprint with your directory administrator, then
   explicitly approve it. Retrieval sends no credentials and does not itself establish
   trust. The preview is bound to that server, stored separately for each endpoint, and expires after ten minutes.
6. Select **Test connection** to validate TLS, the service-account bind and the search
   base for every configured server independently. An error identifies the failing primary or secondary. This does not save settings or prove a user's password and group memberships.
7. Enable LDAPS and select **Save integration**. No pod restart is needed for saved settings.
8. Sign out and select **Sign in with LDAPS**. Test a Viewer first, then the other roles.
   Verify that an account with no mapped group is refused and local sign-in still works.

## Identity, trust and troubleshooting

The service account searches for exactly one user using an escaped username. The user's
password is then checked by binding as that user. LDAP referrals are disabled, and
ambiguous or incomplete searches fail closed. The user's password is never stored.

External identities use the configured directory and immutable directory identifier,
not the login name or email. A directory account cannot take over a local account with
the same name. Local disabling of the provisioned account is respected. Names and roles
are refreshed on each sign-in; existing directory sessions expire after one hour.
Removing a group does not immediately revoke an already authenticated session.
Changing the directory endpoint or identity attribute can create a different identity.

Approved certificate trust is stored in the database and applies across web replicas.
Both certificate validity and hostname are checked. Retrieving a replacement may be
necessary after certificate renewal. Keep `DJANGO_SECRET_KEY` stable and consistent
across replicas so service credentials remain decryptable.

Authentication and configuration events are available in **Audit & diagnostics** and
container logs. Failures include a sanitized reason or exception class; passwords and
raw LDAP responses are never logged. `unmapped_groups` means none of the configured DNs
matched direct `memberOf` values; `user_search_failed` means the search did not return
exactly one complete result. A connection test failure can indicate DNS, TLS, bind
credentials, permissions or an incorrect search base. Verify network access from the
web pod and inspect directory-side logs for the corresponding request.

Implementation reference: [ldap3 TLS documentation](https://ldap3.readthedocs.io/en/latest/ssltls.html).

## Primary and secondary failover

Each sign-in starts with the primary. A connection failure, timeout, TLS failure,
or LDAP busy/unavailable response causes one retry of the complete user lookup and
bind sequence on the secondary, using its own approved certificate trust. Both
service-account and user binds use the same endpoint during each attempt. The
secondary must independently pass TLS certificate and hostname verification.

Invalid credentials, missing or ambiguous users, and unmapped groups do not cause
failover. This avoids repeating rejected passwords against another controller.
Both endpoints must replicate the same user identifiers, groups, search base and
service account. Failover keeps the existing application account because identity
is still scoped to the configured primary directory, not the endpoint responding.
Changing the primary URL still has the existing identity implications described above.

The audit log records `auth.ldap.failover` when the primary is unavailable. If both
servers fail, local sign-in remains available. Configuration testing requires all
configured servers to pass; it does not hide a broken endpoint through failover.
To renew just one certificate, retrieve and approve that server's replacement.
