"""Present stored, already-redacted collection errors without changing audit results."""
import re
from django import template

register = template.Library()


@register.filter
def collection_error(value):
    message = str(value or '').lower()
    if 'incomplete/changing' in message:
        return {'title': 'NSX inventory changed during collection',
                'summary': 'The number of objects returned by NSX did not match its reported inventory total. Changes during collection or search indexing delays can cause this.',
                'action': 'Allow policy and object changes to finish and the NSX search index to settle, then run the collection again. If it keeps happening without changes, check NSX search health.'}
    if any(term in message for term in ('name or service not known', 'temporary failure in name resolution',
                                        'nodename nor servname', 'getaddrinfo failed')):
        return {'title': 'NSX Manager address could not be resolved',
                'summary': 'The collector could not find the network address for this NSX Manager.',
                'action': 'Check the manager hostname, network or VPN connection, and DNS access from the collection server.'}
    if any(term in message for term in ('certificate_verify_failed', 'certificate verify failed', 'hostname mismatch')):
        return {'title': 'NSX Manager certificate could not be verified',
                'summary': 'The secure connection failed certificate verification.',
                'action': 'Check the manager hostname, certificate expiry, and uploaded CA certificate in environment settings.'}
    if re.search(r'\bhttp 401\b', message):
        return {'title': 'NSX Manager sign-in failed',
                'summary': 'NSX Manager rejected the supplied credentials.',
                'action': 'Check the saved username and password and confirm the NSX account is enabled.'}
    if re.search(r'\bhttp 403\b', message):
        return {'title': 'NSX Manager access was denied',
                'summary': 'The collection account was not allowed to read the requested data.',
                'action': 'Check the account permissions for Policy inventory and search.'}
    if any(term in message for term in ('network is unreachable', 'no route to host', 'connection refused',
                                        'connection reset', 'host is unreachable')):
        return {'title': 'Could not connect to NSX Manager',
                'summary': 'The network connection to NSX Manager could not be established or was interrupted.',
                'action': 'Check the network or VPN connection, manager availability, and network access from the collection server.'}
    if 'timed out' in message or 'timeout' in message:
        return {'title': 'Collection timed out',
                'summary': 'The collection could not finish within the allowed time.',
                'action': 'Check connectivity and NSX Manager responsiveness. Retry once the connection is stable.'}
    if re.search(r'\bhttp 429\b', message):
        return {'title': 'NSX Manager is limiting requests',
                'summary': 'NSX Manager asked the collector to reduce its request rate.',
                'action': 'Allow the manager to recover, then retry the collection.'}
    if re.search(r'\bhttp 5\d\d\b', message):
        return {'title': 'NSX Manager returned a server error',
                'summary': 'NSX Manager could not complete a requested operation.',
                'action': 'Check manager health and retry. If the error continues, use the technical details for troubleshooting.'}
    return {'title': 'Collection could not be completed',
            'summary': 'An error prevented this collection from completing.',
            'action': 'Review the technical details below to identify the next step.'}
