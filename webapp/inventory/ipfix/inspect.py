"""Bounded IPFIX v10 datagram/template inspection for the DFW proof of concept.

Data sets are reported by ID/size, never interpreted without exporter-scoped
state. Enterprise fields and duplicate information elements retain their order.
This is a validation primitive, not a production collector or traffic decoder.
"""
import struct


class InvalidMessage(ValueError):
    """The datagram cannot be safely interpreted as an IPFIX message."""


def inspect_message(data):
    """Inspect one UDP payload without retaining templates, flows or raw data."""
    if not isinstance(data, bytes) or not 16 <= len(data) <= 65535:
        raise InvalidMessage("Expected an IPFIX message between 16 and 65535 bytes")
    version, length, exported, sequence, domain = struct.unpack_from('!HHIII', data)
    if version != 10 or length != len(data):
        raise InvalidMessage("Unsupported version or mismatched message length")
    sets = []
    offset = 16
    while offset < length:
        if offset + 4 > length:
            raise InvalidMessage("Truncated set header")
        set_id, size = struct.unpack_from('!HH', data, offset)
        if size < 4 or offset + size > length:
            raise InvalidMessage("Invalid set length")
        body = data[offset + 4:offset + size]
        row = {'id': set_id, 'bytes': size}
        if set_id in (2, 3):
            row['kind'] = 'templates' if set_id == 2 else 'options_templates'
            row['templates'] = _templates(body, options=set_id == 3)
        elif set_id >= 256:
            row['kind'] = 'data'
            row['decoded'] = False
        else:
            raise InvalidMessage("Reserved set ID")
        sets.append(row)
        offset += size
    return {'version': version, 'export_time_seconds': exported, 'sequence': sequence,
            'observation_domain_id': domain, 'sets': sets}


def _templates(body, options):
    rows = []
    offset = 0
    while offset < len(body):
        remaining = len(body) - offset
        if remaining <= 3 and not any(body[offset:]):
            break  # Set padding must be zero and shorter than a record header.
        if remaining < 4:
            raise InvalidMessage("Truncated template header")
        template, count = struct.unpack_from('!HH', body, offset)
        offset += 4
        # RFC 7011 forbids Template Withdrawal Messages over UDP.
        if template < 256 or count == 0:
            raise InvalidMessage("Invalid template ID or UDP template withdrawal")
        scope = 0
        if options:
            if offset + 2 > len(body):
                raise InvalidMessage("Missing options scope count")
            scope, = struct.unpack_from('!H', body, offset)
            offset += 2
            if not 1 <= scope <= count:
                raise InvalidMessage("Invalid options scope count")
        if count > 1024:
            raise InvalidMessage("Template exceeds proof-of-concept field limit")
        fields = []
        for index in range(count):
            if offset + 4 > len(body):
                raise InvalidMessage("Truncated field specifier")
            element, size = struct.unpack_from('!HH', body, offset)
            offset += 4
            enterprise = None
            if element & 0x8000:
                if offset + 4 > len(body):
                    raise InvalidMessage("Missing enterprise number")
                enterprise, = struct.unpack_from('!I', body, offset)
                offset += 4
            if size == 0:
                raise InvalidMessage("Zero-length field")
            fields.append({'element_id': element & 0x7fff, 'enterprise_number': enterprise,
                           'length': size, 'variable_length': size == 65535,
                           'scope': index < scope})
        rows.append({'id': template, 'scope_fields': scope, 'fields': fields})
    return rows
