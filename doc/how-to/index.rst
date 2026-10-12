How-to guides
=============

These guides apply scim2-server to a task whose outcome is already known. Each one starts and
ends where an application resumes its own work, and names what it assumes.

HTTP
----

The integration connects the server to a web framework:

.. toctree::
   :maxdepth: 1

   integrate-a-web-framework

Service
-------

The service applies the rules of SCIM. These guides configure what it accepts and how it answers:

.. toctree::
   :maxdepth: 1

   authenticate-the-clients
   serve-the-me-endpoint
   serve-several-tenants
   page-with-cursors
   publish-the-resources-at-other-urls

Storage
-------

The storage keeps the resources. These guides write one, and check it:

.. toctree::
   :maxdepth: 1

   write-a-storage
   serve-an-existing-data-model
   check-a-storage
   support-cursors-in-a-storage
