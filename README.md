# scim2-server

This is an example WSGI-SCIM server using [scim2-models](https://github.com/python-scim/scim2-models).
It utilizes [werkzeug](https://werkzeug.palletsprojects.com/) and [scim2-filter-parser](https://github.com/15five/scim2-filter-parser) and keeps all resources in-memory,
they are lost once the process exits.

## Features

- [x] Discovery endpoints (`/v2/ServiceProviderConfig`, `/v2/ResourceTypes`, `/v2/Schemas`)
- [x] Create/Read/Update/Delete resources (`POST`, `GET`, `PUT`, `DELETE`)
- [x] Searching & Filtering
- [x] Support for ETags
- [x] Unique Constraints
- [x] HTTP PATCH (Add/Remove/Replace)
- [x] Sorting

The only optional feature currently missing is support for Bulk operations ([RFC 7644, Section 3.7](https://datatracker.ietf.org/doc/html/rfc7644#section-3.7)).

## Usage

```shell
$ scim2-server [-h] [--schema SCHEMA] [--resource-type RESOURCE_TYPE] [--bearer-token BEARER_TOKEN] [--hostname HOSTNAME] [--port PORT] [--reverse-proxy] [--dump-resources DUMP_RESOURCES] [--debug]
```

- `-h`/`--help`: Show help message
- `--reverse-proxy`: Allow using the provider behind a Reverse Proxy (required for URL rewriting).
- `--schema`: Register schemas from specified JSON file. If not provided, loads the default schemas from RFC 7643.
- `--resource-type`: Register resource types from specified JSON file. If not provided, loads the default resource types from RFC 7643.
- `--bearer-token`: Registers a bearer token that can be used for accessing the service. If no tokens are provided, anonymous access without authentication is allowed.
- `--hostname`: The hostname to listen on. Defaults to `127.0.0.1`.
- `--port`: The port to listen on. Defaults to `8080`.
- `--dump-resources`: Dump a JSON document containing all resources when the provider exits normally.
- `--debug`: Enable the interactive Werkzeug debugger, the reloader and the logging of the WSGI environment of each request. The debugger allows arbitrary code execution and the environment contains the bearer tokens, so never use this option on a server reachable by others.

### Container

A container image is published on the GitHub container registry for each release.
The server listens on `0.0.0.0:8080` inside the container, and the command line arguments are passed to `scim2-server`:

```shell
$ docker run --publish 8080:8080 ghcr.io/python-scim/scim2-server --bearer-token secret
```

To build the image yourself, use `docker build --file Containerfile .` or `podman build .`.

## Notes

This provider can be used as a starting point if you want to implement a SCIM provider. You should probably change the following things, if you want to use it in production:

- Use a proper production WSGI server instead of the one provided by Werkzeug
- Implement your own Backend as a subclass of `scim2_server.backend.Backend`
- Implement proper authorization with OAuth instead of public access or static bearer tokens
- Support the `/Me` endpoint, if it applies in your use case
- Add support for using either a static URL prefix or improve the support for usage behind a reverse proxy

The provider in its current state has been tested successfully against a live
[Microsoft Entra](https://learn.microsoft.com/en-us/entra/identity/app-provisioning/scim-validator-tutorial)
system as well as a live
[Okta](https://developer.okta.com/docs/guides/scim-provisioning-integration-test/main/) system.

## Origins

Parts of this software were initially developed at [CONTACT Software](https://www.contact-software.com/) ([GitHub](https://github.com/cslab)) and subsequently made available under the Apache License Version 2.0.
