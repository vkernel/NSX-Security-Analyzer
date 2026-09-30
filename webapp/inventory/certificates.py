"""Credential-free NSX certificate retrieval with explicit user trust review."""
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization


class DiscoveryError(ValueError):
    pass


def origin(value):
    try:
        parsed = urlsplit(value if '://' in value else 'https://' + value)
        port = parsed.port or 443
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError()
        return parsed.hostname, port
    except ValueError:
        raise DiscoveryError('Enter a NSX Manager HTTPS hostname or origin URL.') from None


def retrieve_manager(server):
    """Retrieve only the presented HTTPS certificate, without sending credentials."""
    import socket
    host, port = origin(server)
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with ssl._create_unverified_context().wrap_socket(raw, server_hostname=host) as connection:
                der = connection.getpeercert(binary_form=True)
        cert = x509.load_der_x509_certificate(der)
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
            raise DiscoveryError('The Manager certificate is expired or not yet valid. Correct its certificate or the application clock before trusting it.')
        fp = cert.fingerprint(hashes.SHA256()).hex().upper()
        return {'pem': cert.public_bytes(serialization.Encoding.PEM).decode(), 'certificates': [
            {'subject': cert.subject.rfc4514_string(), 'issuer': cert.issuer.rfc4514_string(),
             'fingerprint': ':'.join(fp[i:i+2] for i in range(0, len(fp), 2)),
             'expires': cert.not_valid_after_utc.isoformat()}]}
    except DiscoveryError:
        raise
    except Exception:
        raise DiscoveryError('Could not retrieve the Manager certificate. Check its HTTPS hostname and connectivity.') from None
