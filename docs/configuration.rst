Configuration
=============

The application reads settings from environment variables (and loads a local
``.env`` file when present). ``DATABASE_URL`` is needed to serve requests.
JWT settings are validated when tokens are created or verified.

Authentication
--------------

``JWT_SECRET_KEY`` must contain at least 32 UTF-8 bytes. ``JWT_ALGORITHM``
supports ``HS256`` (the default), ``HS384``, and ``HS512``.
``JWT_ACCESS_TOKEN_EXPIRE_MINUTES`` defaults to ``15`` and
``JWT_REFRESH_TOKEN_EXPIRE_DAYS`` defaults to ``7``; both must be positive
integers.

Database and integrations
-------------------------

* ``DATABASE_URL`` selects the SQLAlchemy database.
* ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USERNAME``, ``SMTP_PASSWORD``,
  ``SMTP_FROM_EMAIL``, and ``SMTP_USE_STARTTLS`` configure email delivery.
  The local defaults target ``localhost:1025`` without authentication.
* ``APP_BASE_URL`` is used to construct verification and password-reset links.
* ``REDIS_HOST``, ``REDIS_PORT``, ``REDIS_DB``, ``REDIS_PASSWORD``, and
  ``REDIS_CACHE_TTL_SECONDS`` configure the optional user cache. Redis failures
  are logged and do not prevent database-backed authorization.
* ``CLOUDINARY_CLOUD_NAME``, ``CLOUDINARY_API_KEY``, and
  ``CLOUDINARY_API_SECRET`` configure avatar uploads.
* ``ALLOWED_ORIGINS`` is a comma-separated list of CORS origins.

Rate limits
-----------

``CONTACT_REQUESTS_PER_MINUTE`` sets the per-user, route, and method limit
(default ``60``). ``CONTACT_CREATES_PER_MINUTE`` sets an additional
per-user contact-creation limit (default ``10``). Both must be positive
integers.
