"""Test an unsaved NSX connection with one bounded, read-only policy request."""
from django.core.exceptions import ValidationError
from .collector import NSXClient, AuditError
from .credentials import decrypt_password
from .certificates import origin


def test_credentials(form, saved, approved_certificate=None):
    values = form.cleaned_data
    password = values.get('password')
    if not password:
        if not saved or origin(form.instance.manager) != origin(saved['manager']) or values['username'] != saved['username']:
            return False, 'Enter the password for the new account or Manager before testing.'
        try:
            password = decrypt_password(saved['password'])
        except ValidationError:
            return False, 'The saved password cannot be read. Enter it again before testing.'
    upload = values.get('ca_upload')
    pem = approved_certificate or (upload['pem'] if upload else form.instance.ca_certificate)
    bundle = form.instance.ca_bundle
    if values.get('remove_ca'):
        pem, bundle = '', ''
    client = None
    try:
        client = NSXClient(form.instance.manager, values['username'], password,
            timeout=min(values['timeout'], 10), retries=0, insecure=values['insecure'],
            ca_data=pem or None, ca_bundle=None if pem else bundle or None)
        response = client.get('/infra/domains', {'page_size': 1})
        if not isinstance(response.get('results'), list):
            return False, 'The Manager returned an unexpected response; credentials could not be verified.'
        return True, 'Credentials accepted and NSX Policy API read access verified. Nothing was saved. This does not verify all collection or deletion permissions.'
    except AuditError as exc:
        if exc.status_code == 401:
            return False, 'NSX rejected the credentials (HTTP 401). Check the username and password.'
        if exc.status_code == 403:
            return False, 'NSX denied this API request (HTTP 403). Check the service account permissions.'
        if exc.status_code:
            return False, f'NSX returned HTTP {exc.status_code}. Credentials could not be verified.'
        return False, 'Could not verify the connection. Check Manager reachability, timeout and TLS certificate settings.'
    except (OSError, ValueError):
        return False, 'Could not initialize the secure connection. Check the certificate and connection settings.'
    finally:
        if client is not None:
            client.close()
