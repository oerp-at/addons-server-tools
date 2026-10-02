# base_exception

## Purpose

Generic framework for blocking or warning "exceptions" on business documents (sale
orders, invoices, ...). Concrete modules (e.g. `sale_exception`) inherit the abstract
model and register their own rules and checks.

## Models

- `base.exception.method` (abstract): rule evaluation engine. `detect_exceptions()`
  collects active `exception.rule` records for the model and evaluates them per record.
- `base.exception` (abstract, inherits the method model): mixin for documents. Fields
  `exception_ids`, `main_exception_id`, `exceptions_summary`, `ignore_exception`.
  `_check_exception()` raises / opens the confirm wizard when blocking rules match.
- `exception.rule`: the configurable rule. `model` and `method` are empty selections
  that inheriting modules extend. `exception_type`:
  - `by_domain`: domain on the main records,
  - `by_py_code`: python code evaluated with `safe_eval` (sets `failed = True`),
  - `by_method`: calls a method named in `method` on the records. `is_blocking` decides
    between hard stop and confirmable warning.

## Rules

- Rules per domain are cached with `api.ormcache("domain", "self.env.lang")`. Every
  create/write/unlink of a rule must call `self.env.transaction.invalidate_ormcache()`,
  otherwise changed rules are not seen.
- `ignore_exception` is reset by inheriting modules when the document changes again.
- Rule menus (here and in `sale_exception` etc.) are restricted to
  `group_exception_rule_manager`. Root and admin get the group on install, like Odoo's
  own manager groups; every other user must be added explicitly (user form, debug mode).

## Pitfalls

- 20.0: `tools.ormcache` is deprecated, use `api.ormcache`; `registry.clear_cache()` was
  replaced by `env.transaction.invalidate_ormcache()`.
- 20.0: access rights live in `security/ir.access.csv` (`operation` = `crud` letters),
  not `ir.model.access.csv`.
- Tests register fake models (`tests/purchase_test.py`) with
  `odoo.orm.model_classes.add_to_registry`; the module must be installed before running
  them, otherwise `setUpClass` fails with `KeyError: 'exception.rule'`.
