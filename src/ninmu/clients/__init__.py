"""Focused client wrappers for the upstream APIs."""

from ninmu.clients.notediscovery import NoteDiscoveryClient, UpstreamError
from ninmu.clients.vikunja import VikunjaClient

__all__ = ["NoteDiscoveryClient", "UpstreamError", "VikunjaClient"]
