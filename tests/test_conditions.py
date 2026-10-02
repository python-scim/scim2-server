import pytest
from scim2_models import PreconditionFailedException

from scim2_server.conditions import Conditions
from scim2_server.conditions import EntityTags
from scim2_server.conditions import opaque_tag


@pytest.mark.parametrize(
    ("entity_tag", "opaque"),
    [
        ('W/"abc"', "abc"),
        ('w/"abc"', "abc"),
        ('"abc"', "abc"),
        ("abc", "abc"),
        (' W/"abc" ', "abc"),
        ('"', '"'),
    ],
)
def test_opaque_tag(entity_tag, opaque):
    """The opaque part of a tag leaves out its weak prefix and its quotes."""
    assert opaque_tag(entity_tag) == opaque


@pytest.mark.parametrize("header", [None, "", "   "])
def test_missing_header(header):
    """A missing or empty header holds no condition."""
    assert EntityTags.parse(header) is None


@pytest.mark.parametrize("header", ["*", ' W/"abc", * '])
def test_star(header):
    """A "*" member makes the header match any current representation."""
    assert EntityTags.parse(header) == EntityTags(any=True)


@pytest.mark.parametrize(
    ("header", "tags"),
    [
        ('W/"abc"', {"abc"}),
        ('"abc", W/"def"', {"abc", "def"}),
        ('"a,b", "c"', {"a,b", "c"}),
        ('w/"abc"', {"abc"}),
        ("abc, def", {"abc", "def"}),
        ("abc,, def ,", {"abc", "def"}),
        ('""', {""}),
        ('"unterminated', {'"unterminated'}),
    ],
)
def test_tag_list(header, tags):
    """A header lists the opaque part of its tags, quoted commas included."""
    assert EntityTags.parse(header) == EntityTags(tags=frozenset(tags))


def test_weak_and_strong_tags_match_alike():
    """Weak comparison ignores the weak prefix on both sides."""
    tags = EntityTags.parse('"abc"')
    assert tags.matches('W/"abc"')
    assert not tags.matches('W/"def"')


def test_unversioned_resource_matches_only_star():
    """A resource without version matches no listed tag, and still matches "*"."""
    assert not EntityTags.parse('""').matches(None)
    assert EntityTags.parse("*").matches(None)


def test_no_condition_passes():
    """A request without conditional header is performed."""
    assert Conditions().check('W/"abc"', "PUT")


def test_matching_if_match_passes():
    """A matching If-Match lets the method be performed."""
    assert Conditions(if_match='W/"abc"').check('W/"abc"', "PUT")


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_failed_if_match_answers_412(method):
    """A failed If-Match answers 412 whatever the method."""
    with pytest.raises(PreconditionFailedException):
        Conditions(if_match='W/"def"').check('W/"abc"', method)


def test_failed_if_none_match_on_get_answers_304():
    """A failed If-None-Match on a GET answers 304."""
    assert not Conditions(if_none_match='W/"abc"').check('W/"abc"', "GET")


def test_failed_if_none_match_on_put_answers_412():
    """A failed If-None-Match on another method than GET answers 412."""
    with pytest.raises(PreconditionFailedException):
        Conditions(if_none_match="*").check('W/"abc"', "PUT")


def test_if_match_is_evaluated_first():
    """A failed If-Match answers 412 even on a GET whose If-None-Match also fails."""
    with pytest.raises(PreconditionFailedException):
        Conditions(if_match='"def"', if_none_match='"abc"').check('W/"abc"', "GET")


def test_passing_if_none_match():
    """A non matching If-None-Match lets the method be performed."""
    assert Conditions(if_none_match='W/"def"').check('W/"abc"', "GET")
