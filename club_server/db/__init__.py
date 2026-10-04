"""Database package.

Deliberately imports nothing: ``engine`` constructs the async engine from
``Settings`` at import time, and alembic imports ``db.base`` and ``db.models``
to autogenerate, which must not require every setting the server needs
(#420). Import ``get_async_session`` / ``async_engine`` from ``.engine`` and
``Base`` from ``.base`` directly, as the application already does.
"""
