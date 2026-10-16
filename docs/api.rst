API and Python Reference
========================

The HTTP API is implemented in :mod:`app.main`; request and response payloads
are defined in :mod:`app.schemas`.

HTTP routes
-----------

.. automodule:: app.main
   :members:

Authentication and authorization
--------------------------------

.. automodule:: app.auth
   :members:

Request and response schemas
----------------------------

.. automodule:: app.schemas
   :members:

Database models
---------------

.. automodule:: app.models
   :members:

Application settings
--------------------

.. automodule:: app.config
   :members:

Database session management
----------------------------

.. automodule:: app.database
   :members: get_db

Supporting services
-------------------

.. automodule:: app.mailer
   :members:

.. automodule:: app.cloudinary_service
   :members:

.. automodule:: app.user_cache
   :members:

.. automodule:: app.rate_limit
   :members:

.. automodule:: app.backfill_contacts
   :members:
