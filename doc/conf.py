import datetime
import os
import sys
from importlib import metadata

from docutils import nodes

sys.path.insert(0, os.path.abspath(".."))

# -- General configuration ------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinxarg.ext",
    "sphinx_design",
    "sphinx_issues",
    "sphinxcontrib.mermaid",
]

templates_path = ["_templates"]
master_doc = "index"
project = "scim2-server"
year = datetime.datetime.now().strftime("%Y")
copyright = f"{year}, Yaal Coop"
author = "Yaal Coop"
source_suffix = {".rst": "restructuredtext"}

version = metadata.version("scim2_server")
language = "en"
pygments_style = "sphinx"
toctree_collapse = False
autosectionlabel_prefix_document = True
suppress_warnings = ["autosectionlabel.changelog"]
autodoc_member_order = "bysource"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest", None),
    "scim2_models": ("https://scim2-models.readthedocs.io/en/latest/", None),
    "scim2_client": ("https://scim2-client.readthedocs.io/en/latest/", None),
    "scim2_tester": ("https://scim2-tester.readthedocs.io/en/latest/", None),
    "werkzeug": ("https://werkzeug.palletsprojects.com/en/stable/", None),
    "flask": ("https://flask.palletsprojects.com/en/stable/", None),
    "pytest": ("https://docs.pytest.org/en/stable/", None),
}

nitpicky = True

# Shibuya inverts the diagrams in dark mode.
mermaid_light_theme = "neutral"
mermaid_dark_theme = "neutral"
mermaid_init_config = {
    "startOnLoad": False,
    "block": {"useMaxWidth": False},
}
mermaid_fullscreen = False
mermaid_height = "auto"
mermaid_width = "70%"

# Autodoc renders an annotation with the module the object is defined in.
# Sibling documentations only publish the public names, so unresolved
# references are retried with them.
REFERENCE_ALIASES = {
    "PatchOp": "scim2_models.PatchOp",
    "Resource": "scim2_models.Resource",
    "scim2_models.base.BaseModel": "scim2_models.BaseModel",
    "scim2_models.context.Context": "scim2_models.Context",
    "scim2_models.messages.bulk.BulkOperation": "scim2_models.BulkOperation",
    "scim2_models.messages.bulk.BulkRequest": "scim2_models.BulkRequest",
    "scim2_models.messages.patch_op.PatchOp": "scim2_models.PatchOp",
    "scim2_models.messages.search_request.SearchRequest": "scim2_models.SearchRequest",
    "scim2_models.provider.ScimProvider": "scim2_models.ScimProvider",
    "scim2_models.resources.resource.Resource": "scim2_models.Resource",
    "scim2_models.resources.resource_type.ResourceType": "scim2_models.ResourceType",
    "scim2_models.resources.schema.Schema": "scim2_models.Schema",
    "scim2_models.resources.service_provider_config.Bulk": "scim2_models.Bulk",
    "scim2_models.resources.service_provider_config.Filter": "scim2_models.Filter",
    "scim2_models.resources.service_provider_config.Patch": "scim2_models.Patch",
    "scim2_models.resources.service_provider_config.ServiceProviderConfig": "scim2_models.ServiceProviderConfig",
    "scim2_models.resources.service_provider_config.Sort": "scim2_models.Sort",
    "scim2_models.messages.error.Error": "scim2_models.Error",
    "scim2_models.exceptions.PayloadTooLargeException": "scim2_models.PayloadTooLargeException",
}

# Sphinx cannot parse these annotations of the type hints.
nitpick_ignore_regex = [
    ("py:class", r"collections\.abc\.Callable\[\[\]"),
    ("py:class", r"dict\[str"),
    ("py:class", r"collections\.abc\.Mapping\[str"),
]

nitpick_ignore = [
    # The type variables of scim2-server have no documentation page.
    ("py:class", "scim2_server.service.ModelT"),
    ("py:class", "scim2_server.service.DiscoveryResourceT"),
    ("py:class", "scim2_server.testing.T"),
    ("py:class", "ModelT"),
    ("py:class", "DiscoveryResourceT"),
    ("py:class", "T"),
    # The typeshed WSGI types only exist for type checkers.
    ("py:class", "WSGIEnvironment"),
    ("py:class", "StartResponse"),
    ("py:class", "WSGICallable"),
]

# -- Sibling projects ------------------------------------------------------

# Kept identical in every python-scim documentation, so that any divergence
# shows up in a diff.
NAV_LINKS = [
    {
        "title": "Libraries",
        "children": [
            {
                "title": "scim2-server",
                "url": "https://scim2-server.readthedocs.io",
                "summary": "Serve the SCIM protocol over any storage",
            },
            {
                "title": "scim2-client",
                "url": "https://scim2-client.readthedocs.io",
                "summary": "Pythonically build SCIM requests and parse SCIM responses",
            },
            {
                "title": "scim2-models",
                "url": "https://scim2-models.readthedocs.io",
                "summary": "SCIM resources and messages as Pydantic models",
            },
        ],
    },
    {
        "title": "Tools",
        "children": [
            {
                "title": "scim2-tester",
                "url": "https://scim2-tester.readthedocs.io",
                "summary": "Check a SCIM server for RFC compliance",
            },
            {
                "title": "scim2-cli",
                "url": "https://scim2-cli.readthedocs.io",
                "summary": "Query a SCIM server from the command line",
            },
            {
                "title": "pytest-scim2-server",
                "url": "https://github.com/pytest-dev/pytest-scim2-server",
                "summary": "A SCIM2 server fixture for pytest",
            },
        ],
    },
    {
        "title": "Integrations",
        "children": [
            {
                "title": "scim2-flask",
                "url": "https://scim2-flask.readthedocs.io",
                "summary": "Painless SCIM integration for Flask",
            },
            {
                "title": "scim2-django",
                "url": "https://scim2-django.readthedocs.io",
                "summary": "Painless SCIM integration for Django",
            },
            {
                "title": "scim2-fastapi",
                "url": "https://scim2-fastapi.readthedocs.io",
                "summary": "Painless SCIM integration for FastAPI",
            },
            {
                "title": "scim2-sqlalchemy",
                "url": "https://scim2-sqlalchemy.readthedocs.io",
                "summary": "Painless SCIM integration for SQLAlchemy",
            },
        ],
    },
]

# -- Options for HTML output ----------------------------------------------

html_theme = "shibuya"
html_baseurl = "https://scim2-server.readthedocs.io"
html_logo = "_static/python-scim.svg"
html_theme_options = {
    "globaltoc_expand_depth": 2,
    "accent_color": "violet",
    "github_url": "https://github.com/python-scim/scim2-server",
    "mastodon_url": "https://toot.aquilenet.fr/@yaal",
    "nav_links": NAV_LINKS,
}
html_context = {
    "source_type": "github",
    "source_user": "python-scim",
    "source_repo": "scim2-server",
    "source_version": "main",
    "source_docs_path": "/doc/",
}

# -- Options for sphinx-issues -------------------------------------

issues_github_path = "python-scim/scim2-server"


def resolve_reference_aliases(app, env, node, contnode):
    """Point a reference at the public name of its target before it is resolved."""
    alias = REFERENCE_ALIASES.get(node.get("reftarget"))
    if not alias:
        return None

    node["reftarget"] = alias
    node["reftype"] = "obj"
    return None


MDN_HEADERS_URL = "https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/"


def mdn_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    """Link an HTTP header to its MDN page, and render its name as code."""
    reference = nodes.reference(
        rawtext, "", nodes.literal(text, text), refuri=MDN_HEADERS_URL + text
    )
    return [reference], []


def setup(app):
    app.add_role("mdn", mdn_role)
    # 400 runs before the intersphinx handler, which sits at the default 500.
    app.connect("missing-reference", resolve_reference_aliases, priority=400)
