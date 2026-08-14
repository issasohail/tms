import logging

from django.apps import apps
from django.db import connections, models, transaction


logger = logging.getLogger(__name__)


def _storage_key(storage):
    return (
        storage.__class__.__module__,
        storage.__class__.__qualname__,
        str(getattr(storage, "location", "")),
        str(getattr(storage, "base_url", "")),
    )


def _related_file_objects(item):
    yield item
    if item._meta.label_lower == "tenants.tenantregistrationsubmission":
        yield from item.pending_people.all()
        yield from item.pending_vehicle_submissions.all()


def _file_candidates(items):
    candidates = {}
    for item in items:
        for related in _related_file_objects(item):
            for field in related._meta.fields:
                if not isinstance(field, models.FileField):
                    continue
                value = getattr(related, field.name, None)
                name = getattr(value, "name", "") or ""
                if name:
                    candidates[(_storage_key(field.storage), name)] = field.storage
    return candidates


def _delete_unreferenced_files(candidates):
    if not candidates:
        return
    remaining = set(candidates)
    names_by_storage = {}
    for storage_key, name in remaining:
        names_by_storage.setdefault(storage_key, set()).add(name)

    try:
        table_names_by_database = {}
        for model in apps.get_models():
            if model._meta.abstract or model._meta.proxy:
                continue
            database = model._default_manager.db
            if database not in table_names_by_database:
                table_names_by_database[database] = set(
                    connections[database].introspection.table_names()
                )
            if model._meta.db_table not in table_names_by_database[database]:
                continue
            for field in model._meta.fields:
                if not isinstance(field, models.FileField):
                    continue
                storage_key = _storage_key(field.storage)
                names = names_by_storage.get(storage_key)
                if not names:
                    continue
                referenced = set(
                    model._default_manager.filter(
                        **{f"{field.name}__in": names}
                    ).values_list(field.name, flat=True)
                )
                for name in referenced:
                    remaining.discard((storage_key, name))
    except Exception:
        logger.exception(
            "Pending-approval file reference scan failed; physical files were retained."
        )
        return

    for key in remaining:
        storage = candidates[key]
        _storage, name = key
        try:
            storage.delete(name)
        except Exception:
            logger.exception("Could not delete purged pending-approval file %s", name)


def hard_delete_pending_objects(items):
    """Delete transient approval rows and unreferenced physical files after commit."""
    materialized = list(items)
    candidates = _file_candidates(materialized)
    deleted_rows = 0
    with transaction.atomic():
        for item in materialized:
            count, _details = item.delete()
            deleted_rows += count
        transaction.on_commit(lambda: _delete_unreferenced_files(candidates))
    return {"objects": len(materialized), "database_rows": deleted_rows, "files": len(candidates)}
