Paging the results
==================

This page explains what a client gets when it reads a large collection page by page, and why
scim2-server pages with ``startIndex`` and with cursors the way it does. The steps to announce
the cursors are in :doc:`../how-to/page-with-cursors`, and the rules a storage follows are in
:doc:`../how-to/write-a-storage`.

What changes between two pages
------------------------------

Each page is a new request. Between two pages, other clients can create, update or delete
resources. Take four users sorted by name, ``A B C D``, read two by two. The client reads the
first page, ``A B``, and then someone changes the collection before the client reads the
second page.

How the server finds the second page decides what the client sees. There are three levels of
guarantee.

Offsets
   The second page is "the resources from the third one", counted again on the current
   collection. If ``A`` is deleted, the collection becomes ``B C D``, and the second page is
   ``D``: the client never sees ``C``. If ``A0`` is created, the collection becomes
   ``A0 A B C D``, and the second page is ``B C``: the client sees ``B`` twice. A change moves
   resources that have nothing to do with it.

   .. mermaid::
      :align: center

      block-beta
      columns 6
      h0[" "] h1["1"] h2["2"] h3["3"] h4["4"] h5["5"]
      r1["read page 1"] a1["A"] b1["B"] c1["C"] d1["D"] space
      r2["delete A"] b2["B"] c2["C"] d2["D"] space space
      r3["read page 2"] b3["B"] c3["C"] d3["D"] space space
      classDef label fill:none,stroke:none
      classDef page fill:#4f7fd1,color:#fff,stroke:#2b5aa8
      classDef wrong fill:none,stroke:#d14f4f,stroke-width:3px,color:#d14f4f
      classDef pagewrong fill:#4f7fd1,color:#fff,stroke:#d14f4f,stroke-width:3px
      class h0,h1,h2,h3,h4,h5,r1,r2,r3 label
      class a1,b1,d3 page
      class c3 wrong

   .. mermaid::
      :align: center

      block-beta
      columns 6
      h0[" "] h1["1"] h2["2"] h3["3"] h4["4"] h5["5"]
      r1["read page 1"] a1["A"] b1["B"] c1["C"] d1["D"] space
      r2["create A0"] n2["A0"] a2["A"] b2["B"] c2["C"] d2["D"]
      r3["read page 2"] n3["A0"] a3["A"] b3["B"] c3["C"] d3["D"]
      classDef label fill:none,stroke:none
      classDef page fill:#4f7fd1,color:#fff,stroke:#2b5aa8
      classDef wrong fill:none,stroke:#d14f4f,stroke-width:3px,color:#d14f4f
      classDef pagewrong fill:#4f7fd1,color:#fff,stroke:#d14f4f,stroke-width:3px
      class h0,h1,h2,h3,h4,h5,r1,r2,r3 label
      class a1,b1,c3 page
      class b3 pagewrong

Stable pages
   The second page is "the resources after ``B``" in the sort order. If ``A`` is deleted, or if
   ``A0`` is created, the second page is still ``C D``. A resource that exists during the whole
   paging, and whose sort value does not change, is seen exactly once. A resource created or
   deleted meanwhile is seen or not, depending on where it sorts. A resource whose sort value
   changes can be seen twice, or not at all. Each page shows the resources as they are when the
   page is read.

   .. mermaid::
      :align: center

      block-beta
      columns 6
      r1["read page 1"] space a1["A"] b1["B"] c1["C"] d1["D"]
      r2["delete A"] space space b2["B"] c2["C"] d2["D"]
      r3["read page 2"] space space b3["B"] c3["C"] d3["D"]
      classDef label fill:none,stroke:none
      classDef page fill:#4f7fd1,color:#fff,stroke:#2b5aa8
      class r1,r2,r3 label
      class a1,b1,c3,d3 page

   .. mermaid::
      :align: center

      block-beta
      columns 6
      r1["read page 1"] space a1["A"] b1["B"] c1["C"] d1["D"]
      r2["create A0"] n2["A0"] a2["A"] b2["B"] c2["C"] d2["D"]
      r3["read page 2"] n3["A0"] a3["A"] b3["B"] c3["C"] d3["D"]
      classDef label fill:none,stroke:none
      classDef page fill:#4f7fd1,color:#fff,stroke:#2b5aa8
      class r1,r2,r3 label
      class a1,b1,c3,d3 page

Snapshot
   Every page shows the collection as it was when the first page was read. Every resource of
   that moment is seen exactly once, in its state of that moment. If ``B0`` is created, the
   second page of stable pages is ``B0 C``, and the second page of a snapshot is still ``C D``.
   It takes an open transaction or a copy of the results for each paging client, or a database
   that reads as of a past date.

   .. mermaid::
      :align: center

      block-beta
      columns 6
      r1["read page 1"] a1["A"] b1["B"] space c1["C"] d1["D"]
      r2["create B0"] a2["A"] b2["B"] n2["B0"] c2["C"] d2["D"]
      r3["read page 2"] a3["A"] b3["B"] space c3["C"] d3["D"]
      classDef label fill:none,stroke:none
      classDef page fill:#4f7fd1,color:#fff,stroke:#2b5aa8
      class r1,r2,r3 label
      class a1,b1,c3,d3 page

What the RFCs say
-----------------

:rfc:`RFC 7644 §3.4.2.4 <7644#section-3.4.2.4>` defines ``startIndex``, which gives offsets. It
warns the clients: "Because pagination is not stateful, clients MUST be prepared to handle
inconsistent results."

:rfc:`RFC 9865 <9865>` adds the cursors. It requires no more than offsets: it does not require
stable pages, nor a snapshot. A server can announce the cursors, and still return gaps and
duplicates, as with ``startIndex``. The goal of the RFC is to let a server reuse the cursors of
its database or of its API, instead of translating them into ``startIndex``
(:rfc:`RFC 9865 §1 <9865#section-1>`).

Neither RFC defines an order when the client gives no ``sortBy``. Two searches without
``sortBy`` can return the resources in different orders.

What scim2-server does
----------------------

``startIndex`` gives offsets, as :rfc:`RFC 7644 <7644#section-3.4.2.4>` defines it.

The cursors give stable pages. A cursor holds the sort value and the identifier of the last
resource of the page, and the next page starts after them. A storage that cannot give stable
pages only pages by index. The server checks this when it starts: it refuses a storage without
cursors when its configuration announces them.

With cursors, stable pages are the least a storage gives. A storage can give more: a database
that reads as of a past date can keep that date in the position, and give a snapshot. The
server itself keeps no state between two pages. A cursor is an encrypted value that the client
sends back.

Without ``sortBy``, the storage chooses the order. Stable pages need a total order: the storage
orders the resources with the same sort value by a unique value, such as the identifier.

Each page checks the rights of the client again. A cursor gives no right of its own
(:rfc:`RFC 9865 §5.2 <9865#section-5.2>`).
