import sys

import pytest

from scim2_server.testserver.cli import main

OAUTH_OPTIONS = [
    "--oauth-issuer",
    "https://auth.example.com",
    "--oauth-audience",
    "https://scim.example.com",
    "--oauth-jwks-uri",
    "https://auth.example.com/certs",
]


def run(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["scim2-server", *arguments])
    with pytest.raises(SystemExit) as exit_info:
        main()

    return exit_info.value.code


@pytest.mark.parametrize(
    "arguments",
    [OAUTH_OPTIONS[:2], OAUTH_OPTIONS[2:4]],
    ids=["issuer only", "audience only"],
)
def test_the_issuer_and_the_audience_go_together(monkeypatch, capsys, arguments):
    """The issuer and the audience are both needed."""
    assert run(monkeypatch, *arguments) == 2
    assert "--oauth-issuer and --oauth-audience go together" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--oauth-scope", "scim"],
        ["--oauth-jwks-uri", "https://auth.example.com/certs"],
    ],
    ids=["scope", "JWK set URL"],
)
def test_the_other_oauth_options_need_the_issuer(monkeypatch, capsys, arguments):
    """A scope or a JWK set URL alone is refused."""
    assert run(monkeypatch, *arguments) == 2
    assert "need --oauth-issuer" in capsys.readouterr().err


def test_the_oauth_options_need_the_oauth_extra(monkeypatch, capsys):
    """Without authlib, the OAuth options tell how to install the oauth extra."""
    monkeypatch.setitem(sys.modules, "scim2_server.testserver.oauth", None)

    assert run(monkeypatch, *OAUTH_OPTIONS) == 2
    assert "pip install 'scim2-server[oauth]'" in capsys.readouterr().err
