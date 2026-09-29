"""One-shot read-only vCenter discovery. Credentials are never persisted."""
import ipaddress
import ssl
import time
from contextlib import suppress
from urllib.parse import urlsplit
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim


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
        raise DiscoveryError('Enter a vCenter HTTPS hostname or origin URL.') from None


def management_addresses(config):
    selected = set(config.selectedVnic or [])
    result = []
    for nic in config.candidateVnic or []:
        if nic.key not in selected:
            continue
        try:
            address = ipaddress.ip_address(nic.spec.ip.ipAddress)
        except (ValueError, AttributeError, TypeError):
            continue
        if address.version == 4 and not (address.is_unspecified or address.is_loopback or address.is_multicast or address.is_link_local):
            result.append((str(address), nic.device))
    return sorted(set(result))


def discover(server, username, password, ca_data=None):
    host, port = origin(server)
    context = ssl.create_default_context(cadata=ca_data)
    session = view = None
    rows, issues = [], []
    deadline = time.monotonic() + 90
    try:
        session = SmartConnect(host=host, port=port, user=username, pwd=password,
                               sslContext=context, httpConnectionTimeout=10)
        content = session.RetrieveContent()
        if content.about.apiType != 'VirtualCenter':
            raise DiscoveryError('Connect to vCenter, not directly to an ESXi host.')
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
        hosts = list(view.view)
        if len(hosts) > 200:
            issues.append('Preview limited to the first 200 visible hosts. Use manual mapping for additional exporters.')
        clusters = {}
        for host in hosts[:200]:
            if time.monotonic() >= deadline:
                issues.append('Discovery time limit reached; this preview is partial.')
                break
            name = host._moId
            try:
                name = host.name
                parent = host.parent
                if parent._moId not in clusters:
                    clusters[parent._moId] = parent.name
                cluster = clusters[parent._moId]
                config = host.configManager.virtualNicManager.QueryNetConfig('management')
                addresses = management_addresses(config)
                if not addresses:
                    issues.append(name + ': no selected management IPv4 address was returned.')
                for address, device in addresses:
                    rows.append({'host': name, 'cluster': cluster, 'address': address, 'device': device})
                    if len(rows) >= 1000:
                        issues.append('Address preview limit reached.')
                        return rows, issues
            except Exception:
                issues.append(name + ': management interfaces could not be read; check visibility and host connectivity.')
        return sorted(rows, key=lambda r: (r['cluster'], r['host'], r['address'])), issues
    except DiscoveryError:
        raise
    except vim.fault.InvalidLogin:
        raise DiscoveryError('vCenter rejected the credentials. Check the username and password.') from None
    except ssl.SSLCertVerificationError as exc:
        code = exc.verify_code
        if code in (62, 64):
            reason = 'The hostname or IP address does not match the vCenter certificate. Use the DNS name listed in its certificate; uploading a CA does not resolve a name mismatch.'
        elif code in (9, 10):
            reason = 'A certificate in the chain is expired or not yet valid. Check the application clock and renew the affected certificate.'
        elif code in (18, 19, 20, 21):
            reason = 'The vCenter certificate chain is not trusted. Upload the issuing root CA and any required intermediate CA certificates together as PEM. A downloaded server certificate alone is usually not a CA bundle.'
        else:
            reason = 'The certificate chain failed validation. Check its issuing CA, validity dates and certificate constraints.'
        raise DiscoveryError('vCenter TLS verification failed. ' + reason + ' (Verification code: ' + str(code) + ')') from None
    except ssl.SSLError:
        raise DiscoveryError('vCenter TLS verification failed. Upload its trusted CA bundle and verify the hostname.') from None
    except Exception:
        raise DiscoveryError('Could not read vCenter inventory. Check connectivity, trusted CA and read-only permissions.') from None
    finally:
        if view is not None:
            with suppress(Exception):
                view.Destroy()
        if session is not None:
            with suppress(Exception):
                Disconnect(session)
