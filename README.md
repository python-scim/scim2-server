# scim2-server

A Python library that serves the SCIM protocol over any storage, built upon
[scim2-models](https://scim2-models.readthedocs.io), following the
[RFC7643](https://datatracker.ietf.org/doc/html/rfc7643.html) and
[RFC7644](https://datatracker.ietf.org/doc/html/rfc7644.html) specifications.
It validates the requests, applies them to the resources and builds the responses.
The application only reads and writes the resources.

It comes with an in-memory storage, WSGI and ASGI applications with no dependency, and a
`scim2-server` command that serves a test server.

## What's SCIM anyway?

SCIM stands for System for Cross-domain Identity Management, and it is a provisioning protocol.
Provisioning is the action of managing a set of resources across different services, usually users and groups.
SCIM is often used between Identity Providers and applications in completion of standards like OAuth2 and OpenID Connect.
It allows users and groups creations, modifications and deletions to be synchronized between applications.

## Features

- **Resources**: creation, read, replacement, PATCH and deletion
- **Search**: filters, sorting, paging and attribute selection, on a resource type or at the root
- **Bulk requests**: with `bulkId` references and `failOnErrors`
- **ETags**: conditional requests and protection against concurrent writes
- **Discovery**: `ServiceProviderConfig`, `ResourceTypes` and `Schemas`
- **Storages**: an interface to implement, an in-memory storage, and a test suite that checks a storage
- **Sync & Async**: a synchronous and an asynchronous handler, over the same rules
- **Multi-tenancy**: one set of resources per URL prefix

## Installation

```shell
pip install scim2-server
```

## Usage

Serve a test server from the command line:

```shell
scim2-server --port 8080
```

Or build the WSGI application in Python:

```python
from scim2_server.memory import InMemoryStorage
from scim2_server.utils import load_default_provider
from scim2_server.wsgi import WSGIApplication

app = WSGIApplication(InMemoryStorage(), load_default_provider())
```

A container image is published on the GitHub container registry for each release:

```shell
docker run --publish 8080:8080 ghcr.io/python-scim/scim2-server --bearer-token secret
```

The server has been tested against a live
[Microsoft Entra](https://learn.microsoft.com/en-us/entra/identity/app-provisioning/scim-validator-tutorial)
system and a live
[Okta](https://developer.okta.com/docs/guides/scim-provisioning-integration-test/main/) system.

## Documentation

- [Tutorial](https://scim2-server.readthedocs.io/en/latest/tutorial.html) builds an in-memory
  SCIM server and sends it the requests of a SCIM client.
- [How-to guides](https://scim2-server.readthedocs.io/en/latest/how-to/index.html) cover focused
  tasks, such as writing a storage or integrating a web framework.
- [Explanation](https://scim2-server.readthedocs.io/en/latest/explanation/index.html) covers the
  layers of the server, the writes of resources and the bulk requests.
- [Reference](https://scim2-server.readthedocs.io/en/latest/reference.html) lists the public API
  and the options of the `scim2-server` command.

## Contributing

The [contribution page](https://scim2-server.readthedocs.io/en/latest/contributing.html)
describes how to run the tests, the style checks and the documentation build.

scim2-server belongs in a collection of SCIM tools developed by [Yaal Coop](https://yaal.coop),
with [scim2-models](https://github.com/python-scim/scim2-models),
[scim2-client](https://github.com/python-scim/scim2-client),
[scim2-tester](https://github.com/python-scim/scim2-tester) and
[scim2-cli](https://github.com/python-scim/scim2-cli).

## Origins

Parts of this software were initially developed at [CONTACT Software](https://www.contact-software.com/) ([GitHub](https://github.com/cslab)) and subsequently made available under the Apache License Version 2.0.
