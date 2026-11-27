"""What a resolved mapping knows of each attribute.

The backend builds the descriptors, and fills the facts only the ORM knows.
The core derives the rest from the SCIM attribute.
"""

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import TYPE_CHECKING
from typing import Any

from scim2_models import Attribute as SchemaAttribute
from scim2_models import AttributeBinding
from scim2_models import Mutability

from .declaration import mutabilities
from .declaration import never_returned

if TYPE_CHECKING:
    from .mapping import ResourceMapping


class Storage(Enum):
    """How a mapping stores an attribute.

    A derived attribute is read from another resource, such as the display of
    a member. A ref is the $ref of a link, built from its value. A link type
    is the type a Link gives its entries, and a constant a sub-attribute an
    Entry gives its entry: clients may send them, and the storage checks them.
    """

    written = "written"
    read_only = "read only"
    derived = "derived"
    ref = "ref"
    link_type = "link type"
    constant = "constant"


_NO_CONSTANT = object()


@dataclass(eq=False)
class Column:
    """A SCIM attribute stored in a value of a record.

    The backend fills key, writable and the facts of the storage. The core
    reads keyed, nullable, needs_value, related, enumeration, stores and
    fills_none. textual and empty_string are left to the conditions of the
    backend:

    - textual: filters compare the value as text, as for an integer id;
    - keyed: a search by this value goes through its index;
    - nullable: the record can hold no value;
    - needs_value: a new record without value fails in the database;
    - empty_string: the record holds an empty string for no value;
    - related: the value is held by a related record, which may not exist;
    - enumeration: the values the storage accepts, if it restricts them;
    - stores: the name of the record field written, for the checks;
    - fills_none: the backend writes a value of its own for no value, as a
      function converting the values may.

    A constant column always holds the same value, when its holder exists.
    """

    binding: AttributeBinding
    key: str
    writable: bool
    textual: bool = False
    keyed: bool = False
    nullable: bool = True
    needs_value: bool = False
    empty_string: bool = False
    related: bool = False
    enumeration: list[Any] | None = None
    stores: str | None = None
    fills_none: bool = False
    in_entry: bool = False
    constant: Any = _NO_CONSTANT

    @property
    def is_constant(self) -> bool:
        return self.constant is not _NO_CONSTANT

    @property
    def is_string(self) -> bool:
        """Whether the attribute is a SCIM string. A binary value is not one."""
        target = self.binding.target_type
        return (
            target is not None
            and target is not bytes
            and SchemaAttribute.Type.from_python(target) == SchemaAttribute.Type.string
        )

    @property
    def concealed(self) -> bool:
        """Whether clients never read the value. Filters and sorts cannot compare it."""
        return never_returned(self.binding) or Mutability.write_only in mutabilities(
            self.binding
        )

    @property
    def casefolded(self) -> bool:
        return self.is_string and not self.binding.case_exact

    @property
    def required(self) -> bool:
        """Whether a new record needs a value for the attribute."""
        if self.nullable or self.related:
            return False

        return self.needs_value


@dataclass(eq=False)
class Collection:
    """A multi-valued or complex attribute, stored in related records.

    The entries of an owned collection belong to the resource. The others are
    links to the resources of another mapping, the target. The backend fills
    relation, and nullable for a single link: the record can hold no link.
    """

    binding: AttributeBinding
    relation: Any
    columns: dict[str | None, Column] = field(default_factory=dict)
    resource_type: str | None = None
    target: "ResourceMapping | None" = None
    link_type: str | None = None
    type_column: Column | None = None
    nullable: bool = True
    stores: str | None = None

    @property
    def owned(self) -> bool:
        return self.resource_type is None

    @property
    def single(self) -> bool:
        return not self.binding.is_multivalued

    @property
    def simple(self) -> bool:
        """Whether the entries are the values of a multi-valued simple attribute."""
        return None in self.columns

    def column(self, sub_field_name: str | None) -> Column | None:
        if sub_field_name == "type" and self.type_column is not None:
            return self.type_column

        return self.columns.get(sub_field_name)

    @property
    def writable(self) -> bool:
        """Whether writing the resource writes these entries."""
        if self.owned and not any(column.writable for column in self.columns.values()):
            return False

        mutability = self.binding.model.get_field_annotation(
            self.binding.field_name, Mutability
        )
        return bool(mutability != Mutability.read_only)


@dataclass(eq=False)
class EntryColumns:
    """The columns of an Entry, held by the resource record.

    The entry exists when one of its columns has a value.
    """

    binding: AttributeBinding
    columns: dict[str, Column]
    constants: dict[str, Column]
    simple = False

    @property
    def writable(self) -> bool:
        return any(column.writable for column in self.columns.values())


def column_storage(column: Column) -> Storage:
    return Storage.written if column.writable else Storage.read_only


__all__ = [
    "Collection",
    "Column",
    "EntryColumns",
    "Storage",
    "column_storage",
]
