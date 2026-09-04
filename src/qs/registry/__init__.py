"""The Registry: in-memory catalogue of plans and devices (``cmp:registry``)."""

from qs.registry.registry import DeviceEntry, Registry, RegistryError

__all__ = ["DeviceEntry", "Registry", "RegistryError"]
