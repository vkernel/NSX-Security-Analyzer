from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class CertificateBootstrapTests(SimpleTestCase):
    def test_approved_leaf_trust_still_checks_hostname(self):
        import socket
        import ssl
        import tempfile
        import threading
        from pathlib import Path
        from .collector import NSXClient
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'manager.example.invalid')])
        issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic issuer')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('manager.example.invalid')]),critical=False)
            .sign(issuer_key,hashes.SHA256()))
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        client = NSXClient('manager.example.invalid','reader','synthetic-password',ca_data=pem)
        with tempfile.TemporaryDirectory() as folder:
            certificate_path=Path(folder)/'certificate.pem'
            key_path=Path(folder)/'key.pem'
            certificate_path.write_text(pem)
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(certificate_path,key_path)
            for hostname, valid in [('manager.example.invalid',True),('wrong.example.invalid',False)]:
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1',0)); listener.listen(); listener.settimeout(3)
                    def serve():
                        try:
                            raw,_=listener.accept()
                            with raw, server_context.wrap_socket(raw,server_side=True):
                                pass
                        except ssl.SSLError:
                            pass
                    thread=threading.Thread(target=serve)
                    thread.start()
                    try:
                        with socket.create_connection(listener.getsockname(),timeout=3) as raw:
                            if valid:
                                with client.context.wrap_socket(raw,server_hostname=hostname):
                                    pass
                            else:
                                with self.assertRaises(ssl.SSLCertVerificationError):
                                    client.context.wrap_socket(raw,server_hostname=hostname)
                    finally:
                        thread.join(timeout=4)
        client.http.clear()
