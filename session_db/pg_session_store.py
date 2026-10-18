# Copyright (c) Odoo SA 2017
# @author Nicolas Seinlet
# Copyright (c) ACSONE SA 2022
# @author Stéphane Bidoul
import json
import logging
import os
from collections.abc import Iterable

import psycopg2

import odoo
from odoo.http import session as http_session
from odoo.tools import SQL, config

_logger = logging.getLogger(__name__)

lock = None
if odoo.evented:
    import gevent.lock

    lock = gevent.lock.RLock()
elif config["workers"] == 0:
    import threading

    lock = threading.RLock()


def with_lock(func):
    def wrapper(*args, **kwargs):
        try:
            if lock is not None:
                lock.acquire()
            return func(*args, **kwargs)
        finally:
            if lock is not None:
                lock.release()

    return wrapper


def with_cursor(func):
    def wrapper(self, *args, **kwargs):
        tries = 0
        while True:
            tries += 1
            try:
                self._ensure_connection()
                return func(self, *args, **kwargs)
            except (psycopg2.InterfaceError, psycopg2.OperationalError):
                self._close_connection()
                if tries > 4:
                    _logger.warning(
                        "session_db operation try %s/5 failed, aborting", tries
                    )
                    raise
                _logger.info("session_db operation try %s/5 failed, retrying", tries)

    return wrapper


class PGSessionStore(http_session.SessionStore):
    """Session store keeping the sessions in a PostgreSQL table.

    Only the storage primitives are overridden; key generation, rotation and
    the cleanup of rotated sessions are inherited from Odoo's store.
    """

    def __init__(self, uri, session_cls=http_session.Session):
        # no filesystem path: nothing is written to disk
        self.path = None
        self.session_cls = session_cls
        self._uri = uri
        self._cr = None
        self._open_connection()
        self._setup_db()

    def __del__(self):
        self._close_connection()

    @with_lock
    def _ensure_connection(self):
        if self._cr is None:
            self._open_connection()

    @with_lock
    def _open_connection(self):
        self._close_connection()
        cnx = odoo.sql_db.db_connect(self._uri, allow_uri=True)
        self._cr = cnx.cursor()
        self._cr._cnx.autocommit = True

    @with_lock
    def _close_connection(self):
        """Return cursor to the pool."""
        if self._cr is not None:
            try:
                self._cr.close()
            except Exception:
                _logger.debug("session_db could not close its cursor", exc_info=True)
            self._cr = None

    @with_lock
    @with_cursor
    def _setup_db(self):
        self._cr.execute(
            """
                CREATE TABLE IF NOT EXISTS http_sessions (
                    sid varchar PRIMARY KEY,
                    write_date timestamp without time zone NOT NULL,
                    payload text NOT NULL
                )
            """
        )

    def get_session_path(self, sid):
        raise NotImplementedError("session_db does not store sessions on disk")

    @with_lock
    @with_cursor
    def save(self, session):
        payload = json.dumps(dict(session))
        self._cr.execute(
            """
                INSERT INTO http_sessions(sid, write_date, payload)
                    VALUES (%(sid)s, now() at time zone 'UTC', %(payload)s)
                ON CONFLICT (sid)
                DO UPDATE SET payload = %(payload)s,
                              write_date = now() at time zone 'UTC'
            """,
            {"sid": session.sid, "payload": payload},
        )

    @with_lock
    @with_cursor
    def delete(self, session):
        self._cr.execute("DELETE FROM http_sessions WHERE sid=%s", (session.sid,))

    @with_lock
    @with_cursor
    def get(self, sid, *, keep_sid=False):
        # same contract as Odoo's store: an invalid sid always yields a new
        # session, an unknown valid sid is kept only with keep_sid
        if not self.is_valid_session_id(sid):
            return self.new()
        self._cr.execute("SELECT payload FROM http_sessions WHERE sid=%s", (sid,))
        row = self._cr.fetchone()
        if row is None:
            if keep_sid:
                return self.session_cls({}, sid, new=False)
            return self.new()
        try:
            data = json.loads(row[0])
        except ValueError:
            _logger.debug("Could not load session data. Use empty session.")
            data = {}
        return self.session_cls(data, sid, new=False)

    @with_lock
    @with_cursor
    def vacuum(self, max_lifetime=http_session.SESSION_LIFETIME):
        self._cr.execute(
            "DELETE FROM http_sessions "
            "WHERE now() at time zone 'UTC' - write_date > %s",
            (f"{max_lifetime} seconds",),
        )

    @with_lock
    @with_cursor
    def get_missing_session_identifiers(self, identifiers: Iterable[str]) -> set[str]:
        """
        :param identifiers: session identifiers whose existence must be checked
                            identifiers are a part session sid (first 42 chars)
        :return: the identifiers which are not present in the store
        """
        missing_identifiers = set()
        for identifier in set(identifiers):
            self._cr.execute(
                "SELECT sid FROM http_sessions WHERE sid LIKE %s||'%%' LIMIT 1",
                (identifier,),
            )
            if self._cr.rowcount == 0:
                missing_identifiers.add(identifier)
        return missing_identifiers

    @with_lock
    @with_cursor
    def delete_from_identifiers(self, identifiers: Iterable[str]) -> None:
        if isinstance(identifiers, str):
            # ValueError as in the 19.0 module, callers may rely on it
            raise ValueError(  # noqa: TRY004
                "Identifier format incorrect, "
                "did you pass in a string instead of a list?"
            )
        for identifier in identifiers:
            # like Odoo's store: skip anything that is not an identifier, so a
            # crafted value can never widen the LIKE pattern
            if not http_session._session_identifier_re.match(identifier):
                continue
            self._cr.execute(
                "DELETE FROM http_sessions WHERE sid LIKE %s||'%%'", (identifier,)
            )


def _check_sessions_db(cr, sessions):
    """Counterpart of ``odoo.addons.bus.session_helpers.check_sessions``.

    The original opens the session file to cache its stat; with sessions in
    the database there is no file, so every websocket session would count as
    outdated. This version reads the store directly and keeps the token check
    of the original unchanged.
    """
    import hashlib
    import hmac
    import time

    from odoo.tools.misc import consteq

    from odoo.addons.bus.session_helpers import _get_session_token_query_params

    store = http_session.session_store()
    now = time.time()
    resolved_by_sid = {}
    to_check_by_sid = {}
    for stored_session in sessions:
        if (
            stored_session.sid in resolved_by_sid
            or stored_session.sid in to_check_by_sid
        ):
            continue
        if stored_session.uid is None:
            resolved_by_sid[stored_session.sid] = stored_session
            continue
        stored = store.get(stored_session.sid)
        if stored.is_new:
            continue
        if "next_sid" in stored:
            stored = store.get(stored["next_sid"])
            if stored.is_new:
                continue
        store.delete_old_sessions(stored)
        if "deletion_time" in stored and stored["deletion_time"] <= now:
            continue
        to_check_by_sid[stored_session.sid] = stored
    if not to_check_by_sid:
        return resolved_by_sid
    uids = {session.uid for session in to_check_by_sid.values()}
    query_params = _get_session_token_query_params(cr, uids)
    cr.execute(
        SQL(
            "SELECT %(select)s FROM %(from)s %(joins)s WHERE %(where)s "
            "GROUP BY %(group_by)s",
            **query_params,
        ),
    )
    id_idx = next(i for i, column in enumerate(cr.description) if column.name == "id")
    keys_by_uid = {}
    for row in cr.fetchall():
        field_values = tuple(
            (column.name, row[index]) for index, column in enumerate(cr.description)
        )
        key_tuple = tuple((k, v) for k, v in field_values if v is not None)
        keys_by_uid[row[id_idx]] = str(key_tuple).encode()
    for given_sid, stored_session in to_check_by_sid.items():
        key = keys_by_uid.get(stored_session.uid)
        if not key:
            continue
        token = hmac.new(key, stored_session.sid.encode(), hashlib.sha256).hexdigest()
        if consteq(token, stored_session.session_token):
            resolved_by_sid[given_sid] = stored_session
    return resolved_by_sid


_original_session_store_cls = http_session.SessionStore


class SessionStoreFactory(_original_session_store_cls):
    """Replaces ``odoo.http.session.SessionStore``.

    ``odoo.http.session.session_store()`` is cached and imported by name all
    over Odoo, so the function itself cannot be swapped. It instantiates the
    module global ``SessionStore`` on its first call though; building the
    store here routes every caller to the database store. Subclasses (e.g. the
    stores of Odoo's HTTP tests) keep the original behaviour.
    """

    def __new__(cls, *args, **kwargs):
        session_db_uri = os.environ.get("SESSION_DB_URI")
        if cls is SessionStoreFactory and session_db_uri:
            _logger.debug("HTTP sessions stored in: db")
            return PGSessionStore(
                session_db_uri,
                session_cls=kwargs.get("session_cls", http_session.Session),
            )
        return super().__new__(cls)


def _patch_bus():
    try:
        from odoo.addons.bus import bus_dispatcher, session_helpers
    except ImportError:
        return
    session_helpers.check_sessions = _check_sessions_db
    bus_dispatcher.check_sessions = _check_sessions_db


# Monkey patch of standard methods
_logger.debug("Monkey patching session store")
http_session.SessionStore = SessionStoreFactory
# Reset the cache in case a store was already built
http_session.session_store.cache_clear()
if os.environ.get("SESSION_DB_URI"):
    _patch_bus()
