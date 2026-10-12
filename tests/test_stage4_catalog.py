import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.urls import resolve, reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from catalog.models import Attribute, Brand, Category


pytestmark = pytest.mark.django_db


def _client_for(user):
    client = APIClient()
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


def _collection(reference_case):
    _, resource = reference_case
    return f"/api/v1/{resource}/"


@pytest.fixture(params=[(Category, "categories"), (Brand, "brands")])
def reference_case(request):
    return request.param


@pytest.fixture
def reference_record(reference_case):
    model, _ = reference_case
    return model.objects.create(name="Main", slug="main")


@pytest.fixture
def manager_client(manager):
    return _client_for(manager)


@pytest.fixture
def category_tree():
    root = Category.objects.create(name="Root", slug="root")
    child = Category.objects.create(name="Child", slug="child", parent=root)
    grandchild = Category.objects.create(
        name="Grandchild", slug="grandchild", parent=child
    )
    return root, child, grandchild


def test_public_list_is_an_array_of_active_records(api_client, reference_case):
    """Public list hides inactive rows and has stable name/id ordering."""
    model, _ = reference_case
    second = model.objects.create(name="B", slug="b")
    first = model.objects.create(name="A", slug="a")
    model.objects.create(name="Hidden", slug="hidden", is_active=False)

    response = api_client.get(_collection(reference_case))

    assert response.status_code == 200
    assert isinstance(response.data, list)
    assert [item["id"] for item in response.data] == [first.pk, second.pk]
    assert all(item["is_active"] for item in response.data)


@pytest.mark.parametrize("actor", ["guest", "customer", "manager"])
def test_active_detail_is_readable_by_all_roles(
    actor, api_client, customer, manager, reference_case, reference_record
):
    """An active detail is public, with no manager ownership requirement."""
    client = api_client
    if actor == "customer":
        client = _client_for(customer)
    elif actor == "manager":
        client = _client_for(manager)

    response = client.get(f"{_collection(reference_case)}{reference_record.slug}/")

    assert response.status_code == 200
    assert response.data["id"] == reference_record.pk
    assert response.data["slug"] == reference_record.slug


@pytest.mark.parametrize("actor", ["guest", "manager"])
def test_inactive_detail_is_hidden_even_from_manager(
    actor, api_client, manager_client, reference_case, reference_record
):
    """Public detail never exposes an inactive row, even with manager JWT."""
    reference_record.is_active = False
    reference_record.save(update_fields=["is_active"])
    client = api_client if actor == "guest" else manager_client

    response = client.get(f"{_collection(reference_case)}{reference_record.slug}/")

    assert response.status_code == 404


def test_unknown_slug_is_404(api_client, reference_case):
    """Missing slug is not confused with an empty list or a numeric ID."""
    response = api_client.get(f"{_collection(reference_case)}missing/")
    assert response.status_code == 404


def test_manager_creates_reference_with_defaults(manager_client, reference_case):
    """POST creates a shared reference and applies model defaults."""
    model, _ = reference_case
    response = manager_client.post(
        _collection(reference_case),
        {"name": "Created", "slug": "created"},
        format="json",
    )

    assert response.status_code == 201
    obj = model.objects.get(pk=response.data["id"])
    assert obj.name == "Created"
    assert obj.description == ""
    assert obj.is_active is True
    if model is Category:
        assert obj.parent_id is None
        assert response.data["created_at"]
        assert response.data["updated_at"]
    else:
        assert "created_at" not in response.data
        assert "updated_at" not in response.data


def test_manager_can_create_inactive_reference(
    manager_client, api_client, reference_case
):
    """Inactive POST is accepted but the created row is not publicly visible."""
    model, _ = reference_case
    response = manager_client.post(
        _collection(reference_case),
        {"name": "Hidden", "slug": "hidden", "is_active": False},
        format="json",
    )

    assert response.status_code == 201
    assert model.objects.get(pk=response.data["id"]).is_active is False
    assert api_client.get(f"{_collection(reference_case)}hidden/").status_code == 404


def test_max_lengths_and_duplicate_names_are_allowed(manager_client, reference_case):
    """Valid length boundaries and repeated names are not over-restricted."""
    model, _ = reference_case
    for slug in ["a" * 100, "b" * 100]:
        response = manager_client.post(
            _collection(reference_case),
            {"name": "n" * 150, "slug": slug, "description": ""},
            format="json",
        )
        assert response.status_code == 201
    assert model.objects.count() == 2
    assert model.objects.filter(name="n" * 150).count() == 2


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize(
    "actor,expected",
    [("guest", 401), ("customer", 403), ("staff", 403), ("superuser", 200)],
)
def test_write_permissions_match_existing_manager_policy(
    method, actor, expected, customer, reference_case, reference_record
):
    """Guest/customer/staff cannot write; technical superuser can."""
    model, _ = reference_case
    if actor in {"staff", "superuser"}:
        customer.is_staff = actor == "staff"
        customer.is_superuser = actor == "superuser"
        customer.save(update_fields=["is_staff", "is_superuser"])
    client = APIClient() if actor == "guest" else _client_for(customer)
    before_count = model.objects.count()
    url = _collection(reference_case)
    payload = {"name": "Changed", "slug": "new-reference"}
    if method == "patch":
        url += f"{reference_record.pk}/"
        payload = {"name": "Changed"}

    response = getattr(client, method)(url, payload, format="json")

    expected_status = 201 if actor == "superuser" and method == "post" else expected
    assert response.status_code == expected_status
    reference_record.refresh_from_db()
    if actor == "superuser":
        if method == "post":
            assert model.objects.count() == before_count + 1
            assert model.objects.get(slug="new-reference").name == "Changed"
        else:
            assert reference_record.name == "Changed"
    else:
        assert model.objects.count() == before_count
        assert reference_record.name == "Main"


def test_manager_partial_update_preserves_omitted_fields(
    manager_client, reference_case, reference_record
):
    """PATCH changes one field, not the omitted slug or description."""
    reference_record.description = "Keep me"
    reference_record.save()
    response = manager_client.patch(
        f"{_collection(reference_case)}{reference_record.pk}/",
        {"name": "Renamed"},
        format="json",
    )

    assert response.status_code == 200
    reference_record.refresh_from_db()
    assert reference_record.name == "Renamed"
    assert reference_record.slug == "main"
    assert reference_record.description == "Keep me"
    assert reference_record.is_active is True


def test_same_slug_in_patch_is_allowed(
    manager_client, reference_case, reference_record
):
    """UniqueValidator excludes the current row and unchanged slug is allowed."""
    response = manager_client.patch(
        f"{_collection(reference_case)}{reference_record.pk}/",
        {"slug": reference_record.slug},
        format="json",
    )
    assert response.status_code == 200
    reference_record.refresh_from_db()
    assert reference_record.slug == "main"


def test_deactivate_and_reactivate_by_id(
    manager_client, api_client, reference_case, reference_record
):
    """Deactivation retains the row; hidden objects remain editable by ID."""
    model, _ = reference_case
    url = f"{_collection(reference_case)}{reference_record.pk}/"
    response = manager_client.patch(url, {"is_active": False}, format="json")

    assert response.status_code == 200
    reference_record.refresh_from_db()
    assert reference_record.is_active is False
    assert model.objects.filter(pk=reference_record.pk).exists()
    assert api_client.get(f"{_collection(reference_case)}main/").status_code == 404
    assert api_client.get(_collection(reference_case)).data == []

    response = manager_client.patch(url, {"is_active": True}, format="json")

    assert response.status_code == 200
    reference_record.refresh_from_db()
    assert reference_record.is_active is True
    assert api_client.get(f"{_collection(reference_case)}main/").status_code == 200


def test_numeric_slug_and_patch_id_select_different_objects(
    api_client, manager_client, reference_case, reference_record
):
    """The same numeric path means slug for GET and primary key for PATCH."""
    model, _ = reference_case
    by_slug = model.objects.create(name="Numeric", slug=str(reference_record.pk))
    url = f"{_collection(reference_case)}{reference_record.pk}/"

    read_response = api_client.get(url)
    patch_response = manager_client.patch(url, {"name": "By ID"}, format="json")

    assert by_slug.pk != reference_record.pk
    assert read_response.status_code == 200
    assert read_response.data["id"] == by_slug.pk
    assert patch_response.status_code == 200
    assert patch_response.data["id"] == reference_record.pk
    reference_record.refresh_from_db()
    by_slug.refresh_from_db()
    assert reference_record.name == "By ID"
    assert by_slug.name == "Numeric"


def test_numeric_detail_has_one_url_pattern(reference_case):
    """URL resolution does not switch views according to the HTTP method."""
    model, _ = reference_case
    name = "category-detail" if model is Category else "brand-detail"
    url = reverse(f"catalog:{name}", kwargs={"lookup": "123"})
    match = resolve(url)
    assert url == f"{_collection(reference_case)}123/"
    assert match.view_name == f"catalog:{name}"
    assert match.kwargs == {"lookup": "123"}


@pytest.mark.parametrize("lookup", ["not-an-id", "999999999"])
def test_invalid_or_missing_patch_id_is_404(
    lookup, manager_client, reference_case, reference_record
):
    """PATCH never falls back from an invalid/missing ID to a slug lookup."""
    response = manager_client.patch(
        f"{_collection(reference_case)}{lookup}/", {"name": "Wrong"}, format="json"
    )
    assert response.status_code == 404
    reference_record.refresh_from_db()
    assert reference_record.name == "Main"


@pytest.mark.parametrize(
    "detail,method",
    [
        (False, "put"),
        (False, "patch"),
        (False, "delete"),
        (True, "post"),
        (True, "put"),
        (True, "delete"),
    ],
)
def test_unsupported_methods_are_405_for_manager(
    detail, method, manager_client, reference_case, reference_record
):
    """PUT/DELETE and wrong collection/detail methods cannot mutate records."""
    model, _ = reference_case
    url = _collection(reference_case)
    if detail:
        url += f"{reference_record.pk}/"
    before = model.objects.values().get(pk=reference_record.pk)

    response = getattr(manager_client, method)(url, {"name": "Wrong"}, format="json")

    assert response.status_code == 405
    assert model.objects.count() == 1
    assert model.objects.values().get(pk=reference_record.pk) == before


def test_public_head_and_options(api_client, reference_case, reference_record):
    """Standard read-only technical methods remain available without JWT."""
    url = f"{_collection(reference_case)}{reference_record.slug}/"
    head_response = api_client.head(url)
    options_response = api_client.options(url)
    assert head_response.status_code == 200
    assert head_response.content == b""
    assert options_response.status_code == 200
    assert "PATCH" in options_response["Allow"]
    assert "DELETE" not in options_response["Allow"]


@pytest.mark.parametrize("field", ["name", "slug"])
def test_required_fields_on_post(field, manager_client, reference_case):
    """POST requires name and slug; partial update has different semantics."""
    model, _ = reference_case
    payload = {"name": "Valid", "slug": "valid"}
    payload.pop(field)
    response = manager_client.post(_collection(reference_case), payload, format="json")
    assert response.status_code == 400
    assert field in response.data
    assert model.objects.count() == 0


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": "x" * 151}, "name"),
        ({"name": []}, "name"),
        ({"slug": ""}, "slug"),
        ({"slug": "x" * 101}, "slug"),
        ({"slug": "Uppercase"}, "slug"),
        ({"slug": "bad/slug"}, "slug"),
        ({"description": None}, "description"),
        ({"is_active": "sometimes"}, "is_active"),
        ({"id": 123}, "id"),
        ({"owner": 123}, "owner"),
    ],
)
def test_invalid_post_does_not_create_row(
    changes, field, manager_client, reference_case
):
    """Invalid, read-only and unknown fields return a field error without INSERT."""
    model, _ = reference_case
    payload = {"name": "Valid", "slug": "valid", **changes}
    response = manager_client.post(_collection(reference_case), payload, format="json")
    assert response.status_code == 400
    assert field in response.data
    assert model.objects.count() == 0


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"slug": "another"}, "slug"),
        ({"name": ""}, "name"),
        ({"id": 321}, "id"),
        ({"owner": 321}, "owner"),
    ],
)
def test_rejected_patch_preserves_all_fields(
    payload, field, manager_client, reference_case, reference_record
):
    """Failed PATCH changes neither content nor timestamps."""
    model, _ = reference_case
    before = model.objects.values().get(pk=reference_record.pk)
    response = manager_client.patch(
        f"{_collection(reference_case)}{reference_record.pk}/", payload, format="json"
    )
    assert response.status_code == 400
    assert field in response.data
    assert model.objects.values().get(pk=reference_record.pk) == before


def test_inactive_slug_is_still_unique(manager_client, reference_case):
    """Soft deactivation does not free a slug for another POST."""
    model, _ = reference_case
    old = model.objects.create(name="Old", slug="taken", is_active=False)
    response = manager_client.post(
        _collection(reference_case), {"name": "New", "slug": "taken"}, format="json"
    )
    assert response.status_code == 400
    assert "slug" in response.data
    assert model.objects.count() == 1
    old.refresh_from_db()
    assert old.name == "Old"
    assert old.is_active is False


@pytest.mark.parametrize("model", [Category, Brand, Attribute])
def test_database_enforces_unique_slug(model):
    """Direct ORM writes cannot bypass the table's unique slug constraint."""
    model.objects.create(name="First", slug="same")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            model.objects.create(name="Second", slug="same")
    assert model.objects.count() == 1


def test_same_slug_is_allowed_in_different_reference_tables():
    """Uniqueness is per model, not a cross-table catalog namespace."""
    Category.objects.create(name="Fitness", slug="fitness")
    Brand.objects.create(name="Fitness", slug="fitness")
    Attribute.objects.create(name="Fitness", slug="fitness")
    assert Category.objects.count() == 1
    assert Brand.objects.count() == 1
    assert Attribute.objects.count() == 1


def test_manager_creates_child_with_parent(manager_client, category_tree):
    """Parent ID is persisted and represented as a flat ID in the response."""
    root, _, _ = category_tree
    response = manager_client.post(
        "/api/v1/categories/",
        {"name": "New Child", "slug": "new-child", "parent": root.pk},
        format="json",
    )
    assert response.status_code == 201
    child = Category.objects.get(pk=response.data["id"])
    assert child.parent_id == root.pk
    assert response.data["parent"] == root.pk


def test_parent_omitted_in_patch_is_preserved(manager_client, category_tree):
    """Omitting parent in PATCH does not move a category to the root."""
    root, child, _ = category_tree
    response = manager_client.patch(
        f"/api/v1/categories/{child.pk}/", {"name": "Renamed"}, format="json"
    )
    assert response.status_code == 200
    child.refresh_from_db()
    assert child.parent_id == root.pk
    assert child.name == "Renamed"


def test_explicit_null_detaches_parent(manager_client, category_tree):
    """Explicit parent=null makes a root without removing descendants."""
    _, child, grandchild = category_tree
    response = manager_client.patch(
        f"/api/v1/categories/{child.pk}/", {"parent": None}, format="json"
    )
    assert response.status_code == 200
    child.refresh_from_db()
    grandchild.refresh_from_db()
    assert child.parent_id is None
    assert grandchild.parent_id == child.pk


def test_parent_can_be_changed_to_another_root(manager_client, category_tree):
    """Cycle protection still permits a legitimate structural update."""
    _, child, grandchild = category_tree
    other = Category.objects.create(name="Other", slug="other")
    response = manager_client.patch(
        f"/api/v1/categories/{child.pk}/", {"parent": other.pk}, format="json"
    )
    assert response.status_code == 200
    child.refresh_from_db()
    grandchild.refresh_from_db()
    assert child.parent_id == other.pk
    assert grandchild.parent_id == child.pk


@pytest.mark.parametrize("new_parent", ["self", "child", "grandchild", "missing"])
def test_cycle_or_missing_parent_patch_leaves_tree_unchanged(
    new_parent, manager_client, category_tree
):
    """Self/descendant/missing parent is rejected before any field is saved."""
    root, child, grandchild = category_tree
    parent_ids = {
        "self": root.pk,
        "child": child.pk,
        "grandchild": grandchild.pk,
        "missing": 999999999,
    }
    before = list(Category.objects.order_by("id").values())
    response = manager_client.patch(
        f"/api/v1/categories/{root.pk}/",
        {"parent": parent_ids[new_parent], "name": "Must not change"},
        format="json",
    )
    assert response.status_code == 400
    assert "parent" in response.data
    assert list(Category.objects.order_by("id").values()) == before


def test_missing_parent_post_does_not_create_category(manager_client):
    """POST with an unknown foreign key returns 400 without a new row."""
    response = manager_client.post(
        "/api/v1/categories/",
        {"name": "Child", "slug": "child", "parent": 999999999},
        format="json",
    )
    assert response.status_code == 400
    assert "parent" in response.data
    assert Category.objects.count() == 0


def test_inactive_parent_is_accepted_in_post_and_patch(manager_client):
    """Parent selection includes inactive rows by the documented policy."""
    parent = Category.objects.create(name="Hidden", slug="hidden", is_active=False)
    response = manager_client.post(
        "/api/v1/categories/",
        {"name": "Child", "slug": "child", "parent": parent.pk},
        format="json",
    )
    assert response.status_code == 201
    child = Category.objects.get(pk=response.data["id"])
    assert child.parent_id == parent.pk
    other = Category.objects.create(name="Other", slug="other")
    response = manager_client.patch(
        f"/api/v1/categories/{other.pk}/", {"parent": parent.pk}, format="json"
    )
    assert response.status_code == 200
    other.refresh_from_db()
    assert other.parent_id == parent.pk


def test_deactivating_parent_preserves_active_descendants(
    manager_client, api_client, category_tree
):
    """Deactivation is not cascading and all original parent links survive."""
    root, child, grandchild = category_tree
    response = manager_client.patch(
        f"/api/v1/categories/{root.pk}/", {"is_active": False}, format="json"
    )
    assert response.status_code == 200
    assert Category.objects.count() == 3
    root.refresh_from_db()
    child.refresh_from_db()
    grandchild.refresh_from_db()
    assert root.is_active is False
    assert child.is_active is True
    assert grandchild.is_active is True
    assert child.parent_id == root.pk
    assert grandchild.parent_id == child.pk
    response = api_client.get("/api/v1/categories/")
    assert response.status_code == 200
    assert {item["id"] for item in response.data} == {child.pk, grandchild.pk}
    assert api_client.get("/api/v1/categories/root/").status_code == 404
    detail = api_client.get("/api/v1/categories/child/")
    assert detail.status_code == 200
    assert detail.data["parent"] == root.pk


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_category_timestamps_cannot_be_submitted(field, manager_client, category_tree):
    """StrictInputMixin rejects API attempts to overwrite server timestamps."""
    root, _, _ = category_tree
    before = Category.objects.values().get(pk=root.pk)
    response = manager_client.patch(
        f"/api/v1/categories/{root.pk}/",
        {field: "2020-01-01T00:00:00Z"},
        format="json",
    )
    assert response.status_code == 400
    assert field in response.data
    assert Category.objects.values().get(pk=root.pk) == before


def test_database_rejects_direct_self_parent(category_tree):
    """CHECK rejects self-reference even when serializer is bypassed."""
    root, _, _ = category_tree
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Category.objects.filter(pk=root.pk).update(parent_id=root.pk)
    root.refresh_from_db()
    assert root.parent_id is None


def test_database_rejects_missing_parent():
    """The actual foreign key rejects nonexistent parents at commit/check time."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Category.objects.create(name="Bad", slug="bad", parent_id=999999999)
            # PostgreSQL FK may be deferred until transaction end.
            connection.check_constraints()
    assert Category.objects.count() == 0


def test_orm_protects_parent_with_children(category_tree):
    """PROTECT prevents accidental ORM deletion of a referenced parent."""
    root, _, _ = category_tree
    with pytest.raises(ProtectedError):
        root.delete()
    assert Category.objects.count() == 3


def test_existing_indirect_cycle_does_not_hang_validator(manager_client):
    """The visited set rejects an already-corrupt ancestry without looping."""
    a = Category.objects.create(name="A", slug="a")
    b = Category.objects.create(name="B", slug="b", parent=a)
    Category.objects.filter(pk=a.pk).update(parent=b)
    response = manager_client.post(
        "/api/v1/categories/",
        {"name": "C", "slug": "c", "parent": a.pk},
        format="json",
    )
    assert response.status_code == 400
    assert "parent" in response.data
    assert Category.objects.count() == 2


@pytest.mark.parametrize("unit", ["", "kg"])
def test_attribute_has_optional_unit_and_no_product_relation(unit):
    """Attribute stores a characteristic type, not a product-specific value."""
    attribute = Attribute(name="Weight", slug="weight", unit=unit)
    attribute.full_clean()
    attribute.save()
    saved = Attribute.objects.get(pk=attribute.pk)
    assert saved.unit == unit
    assert saved.slug == "weight"


def test_attribute_default_unit_is_empty():
    """An omitted unit uses a single empty-string representation."""
    attribute = Attribute(name="Color", slug="color")
    attribute.full_clean()
    attribute.save()
    assert Attribute.objects.get(pk=attribute.pk).unit == ""


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"name": ""}, "name"),
        ({"name": "x" * 101}, "name"),
        ({"slug": "Uppercase"}, "slug"),
        ({"slug": "bad/slug"}, "slug"),
        ({"unit": "x" * 33}, "unit"),
    ],
)
def test_attribute_model_validation_rejects_invalid_values(changes, field):
    """full_clean validates required fields, slug format and unit length."""
    attribute = Attribute(**{"name": "Weight", "slug": "weight", **changes})
    with pytest.raises(ValidationError) as exc:
        attribute.full_clean()
    assert field in exc.value.message_dict
    assert Attribute.objects.count() == 0


def test_attribute_unit_cannot_be_null_in_database():
    """NOT NULL is checked in SQL, separately from optional blank validation."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Attribute.objects.create(name="Weight", slug="weight", unit=None)
    assert Attribute.objects.count() == 0


def test_attribute_api_is_not_added(api_client):
    """Stage 4 defines no public Attribute endpoint."""
    assert api_client.get("/api/v1/attributes/").status_code == 404
