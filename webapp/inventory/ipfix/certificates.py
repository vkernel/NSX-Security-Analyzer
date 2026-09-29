"""Credential-free CA bootstrap; downloaded certificates require explicit review."""
import io
import ssl
import zipfile
from datetime import datetime, timezone
from urllib.request import build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from .vcenter import origin, DiscoveryError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def parse_bundle(data):
    certificates = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 256 or sum(e.file_size for e in entries) > 2 * 1024 * 1024:
                raise ValueError('Archive too large')
            for entry in entries:
                if entry.is_dir():
                    continue
                if entry.file_size > 131072:
                    raise ValueError('Certificate file too large')
                raw = archive.read(entry)  # Never extract archive paths to disk.
                try:
                    candidates = (x509.load_pem_x509_certificates(raw) if b'-----BEGIN CERTIFICATE-----' in raw
                                  else [x509.load_der_x509_certificate(raw)])
                except ValueError:
                    continue  # CRLs and other archive entries are not trusted certificates.
                for cert in candidates:
                    try:
                        if not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                            continue
                    except x509.ExtensionNotFound:
                        continue
                    now = datetime.now(timezone.utc)
                    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
                        continue
                    fingerprint = cert.fingerprint(hashes.SHA256()).hex().upper()
                    certificates[fingerprint] = cert
        if not certificates or len(certificates) > 32:
            raise ValueError('No eligible CA certificates or too many certificates')
        pem = ''.join(cert.public_bytes(serialization.Encoding.PEM).decode() for cert in certificates.values())
        if len(pem) > 131072:
            raise ValueError('Bundle too large')
        return {'pem': pem, 'certificates': [
            {'subject': cert.subject.rfc4514_string(), 'issuer': cert.issuer.rfc4514_string(),
             'fingerprint': ':'.join(fp[i:i+2] for i in range(0, len(fp), 2)),
             'expires': cert.not_valid_after_utc.isoformat()} for fp, cert in certificates.items()]}
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise DiscoveryError('The download did not contain a supported bundle of currently valid CA certificates. Upload a trusted PEM bundle instead.') from None


def retrieve(server):
    host, port = origin(server)
    authority = '[' + host + ']' if ':' in host else host
    # Bootstrap only: no credentials, cookies, redirects, proxies or automatic trust.
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl._create_unverified_context()))
    try:
        with opener.open('https://' + authority + ':' + str(port) + '/certs/download.zip', timeout=10) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('Archive too large')
    except Exception:
        raise DiscoveryError('Could not retrieve the vCenter CA download. Check the hostname and connectivity, or upload a trusted PEM bundle.') from None
    return parse_bundle(data)


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
