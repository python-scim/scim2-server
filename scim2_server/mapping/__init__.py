"""The base of the storages that map SCIM resources to the records of an ORM.

A backend declares how each attribute is stored, and gives the primitives of
its ORM. The core holds the SCIM rules: the checks of the mappings, the
translation of the filters and of the sorts, the reading of the records and
the checks of the writes. scim2-django and scim2-sqlalchemy build on it.

.. warning::

    This API follows the needs of these backends, and may change in any minor
    version.
"""

from .declaration import Attribute
from .declaration import Entry
from .declaration import Link
from .declaration import Many
from .descriptors import Collection
from .descriptors import Column
from .descriptors import EntryColumns
from .descriptors import Storage
from .filters import Predicates
from .mapping import MappingProblem
from .mapping import ResourceMapping

__all__ = [
    "Attribute",
    "Collection",
    "Column",
    "Entry",
    "EntryColumns",
    "Link",
    "Many",
    "MappingProblem",
    "Predicates",
    "ResourceMapping",
    "Storage",
]
