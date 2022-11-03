from qiskit.providers import ProviderV1 as Provider
from qiskit.providers.providerutils import filter_backends

from .backend import QHAnaBackend


class QHAnaProvider(Provider):
    """A provider for the QHAna backend.

    Implemented following the tutorial https://qiskit.org/documentation/apidoc/providers.html#writing-a-new-provider
    """

    def __init__(self, token=None):
        """Initialize the provider (load all backends)."""
        super().__init__()
        self.token = token
        self._backends = [QHAnaBackend(provider=self)]

    def backends(self, name=None, **kwargs):
        """Get a filtered list of available backends."""
        filters = kwargs.pop("filters", None)
        backends_to_filter = self._backends
        if name:
            backends_to_filter = [b for b in self._backends if b.name == name]
        return filter_backends(backends_to_filter, filters=filters, **kwargs)
