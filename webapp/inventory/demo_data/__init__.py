"""Fully synthetic demonstration data.

``scenario`` generates NSX Policy inventories, ``transport`` serves them to the real
collector without opening a network connection, and ``loader`` publishes the
results through the normal snapshot, finding and browsing-index pipeline. Every
name, address and identifier is generated; nothing is derived from a real
installation.
"""
