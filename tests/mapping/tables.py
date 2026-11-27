from scim2_models import EnterpriseUser
from scim2_models import Group
from scim2_models import User

from scim2_server.mapping import Entry
from scim2_server.mapping import Link
from scim2_server.mapping import Many

from .backend import FakeMapping
from .backend import Owned
from .backend import Reference
from .backend import References
from .backend import Referrers
from .backend import Table

ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"

users = Table("users")
for name in (
    "external_id",
    "display_name",
    "title",
    "nick_name",
    "user_type",
    "preferred_language",
    "given_name",
    "family_name",
    "password",
    "email",
    "employee_number",
):
    users.field(name)
users.field("user_name", null=False)
users.field("active")
users.field("manager")
users.field("created")
users.field("last_modified")
users.field("version", null=False, default=1)
users.field("code", null=False, default="none")
users.field("phone")

emails = Table("emails")
emails.field("user", null=False)
emails.field("value")
emails.field("type", choices=["work", "home", "other"])
emails.field("primary")
emails.field("address", null=False)

groups = Table("groups")
groups.field("external_id")
groups.field("display_name")
groups.field("members")
groups.field("nested")
groups.field("created")
groups.field("last_modified")
groups.field("version", null=False, default=1)

TABLES = (users, emails, groups)

DEFAULT = object()


def users_mapping(changes=None, version=DEFAULT, **options):
    """Return a new mapping of the users, with some paths changed, or removed when None."""
    fields = users.fields
    attributes = {
        "id": users.id,
        "externalId": fields["external_id"],
        "userName": fields["user_name"],
        "displayName": fields["display_name"],
        "title": fields["title"],
        "userType": fields["user_type"],
        "nickName": fields["nick_name"],
        "preferredLanguage": fields["preferred_language"],
        "name.givenName": fields["given_name"],
        "name.familyName": fields["family_name"],
        "active": fields["active"],
        "password": fields["password"],
        "emails": Many(
            Owned(emails, "user"),
            {
                "value": emails.fields["value"],
                "type": emails.fields["type"],
                "primary": emails.fields["primary"],
            },
        ),
        "groups": Link(Referrers(groups.fields["members"]), "Group", type="direct"),
        f"{ENTERPRISE}:employeeNumber": fields["employee_number"],
        f"{ENTERPRISE}:manager": Link(Reference(fields["manager"], users), "User"),
        "meta.created": fields["created"],
        "meta.lastModified": fields["last_modified"],
    }
    attributes |= changes or {}
    attributes = {
        path: value for path, value in attributes.items() if value is not None
    }
    return FakeMapping(
        User[EnterpriseUser],
        users,
        attributes,
        version=fields["version"] if version is DEFAULT else version,
        **options,
    )


def groups_mapping(changes=None, version=DEFAULT, **options):
    fields = groups.fields
    attributes = {
        "id": groups.id,
        "externalId": fields["external_id"],
        "displayName": fields["display_name"],
        "members": [
            Link(References(fields["members"], users), "User", type="User"),
            Link(References(fields["nested"], groups), "Group", type="Group"),
        ],
        "meta.created": fields["created"],
        "meta.lastModified": fields["last_modified"],
    }
    attributes |= changes or {}
    attributes = {
        path: value for path, value in attributes.items() if value is not None
    }
    return FakeMapping(
        Group,
        groups,
        attributes,
        version=fields["version"] if version is DEFAULT else version,
        **options,
    )


WORK_EMAIL = {
    "emails": Entry({"value": users.fields["email"]}, type="work", primary=True)
}
UNDATED = {"meta.created": None, "meta.lastModified": None}
