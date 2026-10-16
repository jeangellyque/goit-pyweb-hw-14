Testing
=======

The test suite combines standard-library ``unittest`` cases with pytest
functional tests. It uses in-memory SQLite databases; SMTP, Redis, and
Cloudinary are mocked so tests do not require external services.

Run all tests and enforce the application coverage target from the project
root:

.. code-block:: console

   .venv/bin/python -m pytest -q tests \
       --cov=app --cov-report=term-missing --cov-fail-under=95

Run the unittest-based tests independently with:

.. code-block:: console

   .venv/bin/python -m unittest discover -s tests -p 'test_*.py'

The functional API tests use FastAPI's ``TestClient`` and require ``httpx``.

Build the HTML documentation from the project root with:

.. code-block:: console

   .venv/bin/sphinx-build -b html docs docs/_build/html
