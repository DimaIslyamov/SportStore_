from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from time import monotonic, sleep

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder


INITIAL = ("accounts", "0001_initial")
LEGACY_FIELDS = (
    "accounts",
    "0002_user_created_at_user_phone_user_role_user_updated_at_and_more",
)
REPLACEMENT = ("accounts", "0002_squashed_0004_stage2_user_fields")
JOINED_AT = datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_accounts_migration_graph_has_one_upgrade_path():
    loader = MigrationLoader(None)

    assert loader.detect_conflicts() == {}
    assert len(loader.graph.leaf_nodes("accounts")) == 1


@pytest.fixture
def migrate_accounts(transactional_db):
    # Schema changes and cleanup are restricted to pytest's disposable database.
    assert connection.settings_dict["NAME"].startswith("test_")

    def migrate_to(target=None, *, original=False):
        executor = MigrationExecutor(connection)
        if original:
            executor.loader = MigrationLoader(connection, replace_migrations=False)
        targets = [target] if target else executor.loader.graph.leaf_nodes("accounts")
        state = executor.migrate(targets)
        return state.apps.get_model("accounts", "User")

    migrate_to(INITIAL)
    try:
        yield migrate_to
    finally:
        call_command("flush", interactive=False, verbosity=0)
        migrate_to()


def test_stage1_upgrade_preserves_users_and_backfills_metadata(migrate_accounts):
    User = migrate_accounts(INITIAL)
    user = User.objects.create(
        email=" Legacy@Example.COM ",
        password="existing-password-hash",
        first_name="Existing",
        last_name="Customer",
        date_joined=JOINED_AT,
        is_staff=True,
    )
    admin = User.objects.create(
        email=" ADMIN@Example.COM ",
        password="existing-admin-password-hash",
        date_joined=JOINED_AT,
        is_staff=True,
        is_superuser=True,
    )
    Group = User._meta.apps.get_model("auth", "Group")
    group = Group.objects.create(name="Existing group")
    user.groups.add(group)
    before = list(User.objects.order_by("pk").values())

    User = migrate_accounts()

    assert User.objects.count() == 2
    for original in before:
        migrated = User.objects.values().get(pk=original["id"])
        for field, value in original.items():
            if field != "email":
                assert migrated[field] == value
        assert migrated["created_at"] == JOINED_AT
        assert migrated["updated_at"] == JOINED_AT
        assert migrated["phone"] == ""
    assert User.objects.get(pk=user.pk).email == "legacy@example.com"
    assert User.objects.get(pk=user.pk).role == "CUSTOMER"
    assert list(User.objects.get(pk=user.pk).groups.values_list("pk", flat=True)) == [
        group.pk
    ]
    assert User.objects.get(pk=admin.pk).email == "admin@example.com"
    assert User.objects.get(pk=admin.pk).role == "MANAGER"
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=admin.pk).update(email="LEGACY@EXAMPLE.COM")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(role="UNKNOWN")


def test_already_applied_0002_upgrade_preserves_existing_fields(migrate_accounts):
    User = migrate_accounts(LEGACY_FIELDS, original=True)
    user = User.objects.create(
        email=" Legacy@Example.COM ",
        password="existing-password-hash",
        first_name="Existing",
        phone="+48123456789",
        role="MANAGER",
        date_joined=JOINED_AT,
    )
    admin = User.objects.create(
        email="admin@example.com",
        password="existing-admin-password-hash",
        date_joined=JOINED_AT,
        is_superuser=True,
    )
    before = User.objects.values().get(pk=user.pk)

    User = migrate_accounts()

    after = User.objects.values().get(pk=user.pk)
    for field, value in before.items():
        if field not in {"email", "created_at", "updated_at"}:
            assert after[field] == value
    assert after["email"] == "legacy@example.com"
    assert after["created_at"] == JOINED_AT
    assert after["updated_at"] == JOINED_AT
    assert User.objects.get(pk=admin.pk).role == "MANAGER"
    assert User.objects.count() == 2
    assert (
        MigrationExecutor(connection).migration_plan(
            MigrationLoader(connection).graph.leaf_nodes("accounts")
        )
        == []
    )
    assert REPLACEMENT in MigrationRecorder(connection).applied_migrations()


def test_normalization_preserves_a_concurrent_email_edit(migrate_accounts):
    User = migrate_accounts(LEGACY_FIELDS, original=True)
    user = User.objects.create(
        email=" Original@Example.COM ",
        password="existing-password-hash",
    )
    started = Event()
    worker_pids = []

    def upgrade():
        worker = connections["default"]
        try:
            with worker.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                worker_pids.append(cursor.fetchone()[0])
            started.set()
            migrate_accounts()
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                editor_pid = cursor.fetchone()[0]
            future = pool.submit(upgrade)
            assert started.wait(timeout=10)
            deadline = monotonic() + 10
            blocked = False
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                        [editor_pid, worker_pids[0]],
                    )
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                sleep(0.01)
            assert blocked, "Migration did not reach the locked user row."
            User.objects.filter(pk=user.pk).update(email="New@Example.COM")
        future.result(timeout=10)

    assert User.objects.get(pk=user.pk).email == "new@example.com"


@pytest.mark.parametrize(
    ("emails", "error"),
    [
        (["Mixed@Example.COM", "mixed@example.com"], "conflicting emails"),
        ([" customer@example.com ", "customer@example.com"], "conflicting emails"),
        (["valid@example.com", "   "], "empty email"),
    ],
)
def test_stage1_email_validation_is_atomic(migrate_accounts, emails, error):
    User = migrate_accounts(INITIAL)
    for email in emails:
        User.objects.create(email=email, password="existing-password-hash")
    before = list(User.objects.order_by("pk").values())

    with pytest.raises(RuntimeError, match=error):
        migrate_accounts()

    assert list(User.objects.order_by("pk").values()) == before
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(
            cursor, "accounts_user"
        )
    assert "role" not in {column.name for column in columns}
    applied = MigrationLoader(connection).applied_migrations
    assert {node for node in applied if node[0] == "accounts"} == {INITIAL}


def test_applied_0002_email_conflict_preserves_users(migrate_accounts):
    User = migrate_accounts(LEGACY_FIELDS, original=True)
    for email in [" customer@example.com ", "customer@example.com"]:
        User.objects.create(email=email, password="existing-password-hash")
    before = list(User.objects.order_by("pk").values())

    with pytest.raises(RuntimeError, match="conflicting emails"):
        migrate_accounts()

    assert list(User.objects.order_by("pk").values()) == before
    applied = MigrationLoader(connection).applied_migrations
    assert {node for node in applied if node[0] == "accounts"} == {
        INITIAL,
        LEGACY_FIELDS,
    }
