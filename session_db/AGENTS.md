# session_db – agent notes

Stores the HTTP sessions in PostgreSQL instead of the filesystem. Loaded as a server
wide module; active only when `SESSION_DB_URI` is set.

## How the patch works (20.0)

- `odoo.http.session.session_store()` is `functools.cache`d and imported by name in core
  and addons. It cannot be replaced, but it instantiates the module global
  `SessionStore` on its first call.
- `pg_session_store.py` swaps that global for `SessionStoreFactory` and clears the
  cache. The factory returns `PGSessionStore` when `SESSION_DB_URI` is set; subclasses
  (Odoo's HTTP test stores) are left alone.
- `PGSessionStore` overrides only storage: `save`, `delete`, `get`, `vacuum`,
  `get_missing_session_identifiers`, `delete_from_identifiers`. `get_session_path`
  raises, nothing may touch the disk.
- `bus.session_helpers.check_sessions` opens the session file to cache its stat. With
  `SESSION_DB_URI` it is replaced (in `session_helpers` and in `bus_dispatcher`, which
  imports it by name) by `_check_sessions_db`. Keep its token check in sync with the
  core function on every Odoo update.

## Tests

`odoo test -m session_db` covers CRUD, retries, `keep_sid`, soft rotation, the factory
and `_check_sessions_db`. Live check: start `odoo serve` with `SESSION_DB_URI` and
`--load=base,rpc,web,session_db`, log in, count the rows in `http_sessions`.
