# scim2-server

This is an example WSGI-SCIM server using [scim2-models](https://github.com/python-scim/scim2-models).
It utilizes [werkzeug](https://werkzeug.palletsprojects.com/) and keeps all resources in-memory,
they are lost once the process exits.

## Features

- [x] Discovery endpoints (`/v2/ServiceProviderConfig`, `/v2/ResourceTypes`, `/v2/Schemas`)
- [x] Create/Read/Update/Delete resources (`POST`, `GET`, `PUT`, `DELETE`)
- [x] Searching & Filtering
- [x] Support for ETags
- [x] Unique Constraints
- [x] HTTP PATCH (Add/Remove/Replace)
- [x] Sorting
- [x] Bulk operations
- [x] Multi-tenancy with a URL prefix

## Usage

The `scim2-server` command and the WSGI application need the `werkzeug` extra:

```shell
$ pip install scim2-server[werkzeug]
```

```shell
$ scim2-server [-h] [--schema SCHEMA] [--resource-type RESOURCE_TYPE] [--service-provider-config SERVICE_PROVIDER_CONFIG] [--bearer-token BEARER_TOKEN] [--hostname HOSTNAME] [--port PORT] [--reverse-proxy] [--dump-resources DUMP_RESOURCES] [--tenant TENANT] [--dynamic-tenants] [--debug]
```

- `-h`/`--help`: Show help message
- `--reverse-proxy`: Allow using the provider behind a Reverse Proxy (required for URL rewriting).
- `--schema`: Register schemas from specified JSON file. If not provided, loads the default schemas from RFC 7643.
- `--resource-type`: Register resource types from specified JSON file. If not provided, loads the default resource types from RFC 7643.
- `--service-provider-config`: Load the service provider configuration from specified JSON file. If not provided, loads the default configuration.
- `--bearer-token`: Registers a bearer token that can be used for accessing the service, and announces the bearer token authentication scheme. If no tokens are provided, anonymous access without authentication is allowed.
- `--hostname`: The hostname to listen on. Defaults to `127.0.0.1`.
- `--port`: The port to listen on. Defaults to `8080`.
- `--dump-resources`: Dump a JSON document containing all resources when the provider exits normally. With tenants, the document has one entry per tenant.
- `--tenant`: Serve a tenant under `/<tenant>`, for example `/<tenant>/v2/Users`. Can be repeated. See [Multi-tenancy](#multi-tenancy).
- `--dynamic-tenants`: Create a tenant on the first request to `/<tenant>`. See [Multi-tenancy](#multi-tenancy).
- `--debug`: Enable the interactive Werkzeug debugger, the reloader and the logging of the WSGI environment of each request. The debugger allows arbitrary code execution and the environment contains the bearer tokens, so never use this option on a server reachable by others.

### Multi-tenancy

With `--tenant` or `--dynamic-tenants`, the first segment of the URL path selects a tenant (RFC 7644 §6.1).
Each tenant has its own resources: `/a/v2/Users` and `/b/v2/Users` are separate, and uniqueness, filters and pagination only consider the resources of the tenant.
The schemas, the resource types and the service provider configuration are the same for every tenant, and so are the bearer tokens.
A request without a known tenant gets a 404 answer, and `v2` cannot be a tenant name.

```shell
$ scim2-server --tenant a --tenant b
$ curl http://localhost:8080/a/v2/Users
```

With `--dynamic-tenants`, the first request to an unknown tenant creates it with no resources.
This is useful for tests: each test can pick a random tenant and get an empty server.
Any client can then create tenants, even without a valid bearer token, and every tenant stays in memory until the server exits.
Do not use this option on a server reachable by untrusted clients.

In Python, `scim2_server.werkzeug.TenantDispatcher` builds one `SCIMApplication` per tenant from a factory, on the first request to the tenant.
The factory gets the tenant name and returns `None` when the tenant does not exist.
Override its `select_tenant` method to read the tenant from a header or a sub-domain instead.

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
- Implement your own storage as a subclass of `scim2_server.storage.ScimStorage`, and check it with `scim2_server.testing.ScimStorageContract`
- Implement proper authorization with OAuth instead of public access or static bearer tokens
- Support the `/Me` endpoint, if it applies in your use case

The provider in its current state has been tested successfully against a live
[Microsoft Entra](https://learn.microsoft.com/en-us/entra/identity/app-provisioning/scim-validator-tutorial)
system as well as a live
[Okta](https://developer.okta.com/docs/guides/scim-provisioning-integration-test/main/) system.

## Origins

Parts of this software were initially developed at [CONTACT Software](https://www.contact-software.com/) ([GitHub](https://github.com/cslab)) and subsequently made available under the Apache License Version 2.0.
