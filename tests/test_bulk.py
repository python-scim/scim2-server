from scim2_models import Context
from scim2_models import Group
from scim2_models import GroupMember
from scim2_models import User

from scim2_server.bulk import replace_bulk_ids


def test_a_value_without_reference_is_kept_as_it_is():
    """Nothing is copied when there is no bulkId reference to replace."""
    group = Group(display_name="g", members=[GroupMember(value="1")])
    assert replace_bulk_ids(group, lambda bulk_id: "x") is group


def test_references_are_replaced_in_models_lists_and_dicts():
    """A reference is replaced wherever the value holds it."""
    group = Group(members=[GroupMember(value="bulkId:u")])
    replaced = replace_bulk_ids(
        {"group": group, "ids": ["bulkId:u", 1]}, lambda bulk_id: f"id-{bulk_id}"
    )
    assert replaced["group"].members[0].value == "id-u"
    assert replaced["ids"] == ["id-u", 1]
    assert group.members[0].value == "bulkId:u"


def test_the_fields_the_client_set_stay_the_same():
    """A PUT keeps the write-only values the client left out, so a replaced reference must not set more fields."""
    user = User.model_validate(
        {"userName": "bjensen", "nickName": "bulkId:u"},
        scim_ctx=Context.RESOURCE_REPLACEMENT_REQUEST,
    )
    replaced = replace_bulk_ids(user, lambda bulk_id: "id")
    assert replaced.nick_name == "id"
    assert replaced.model_fields_set == user.model_fields_set
