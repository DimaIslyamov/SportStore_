from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError

from catalog.models import (
    Attribute,
    Brand,
    Category,
    Inventory,
    Product,
    ProductAttributeValue,
    ProductImage,
    ProductVariant,
    VariantAttribute,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def category(db):
    return Category.objects.create(name="Running", slug="running")


@pytest.fixture
def brand(db):
    return Brand.objects.create(name="SportLab", slug="sportlab")


@pytest.fixture
def product(category, brand):
    return Product.objects.create(
        category=category, brand=brand, name="Test Product", slug="test-product"
    )


@pytest.fixture
def variant(product):
    return ProductVariant.objects.create(
        product=product, sku="BASE-01", price=Decimal("12.50")
    )


@pytest.fixture
def attribute(db):
    return Attribute.objects.create(name="Max speed", slug="max-speed", unit="km/h")


@pytest.fixture(params=["product", "variant"])
def value_case(request, product, variant):
    if request.param == "product":
        return ProductAttributeValue, "product", product
    return VariantAttribute, "variant", variant


def test_product_and_variants_have_correct_relations(product, variant, category, brand):
    """Product reuses references and owns several independently priced variants."""
    other = ProductVariant.objects.create(
        product=product, sku="SECOND-01", price=Decimal("15.00")
    )
    assert product.category_id == category.pk
    assert product.brand_id == brand.pk
    assert list(category.products.all()) == [product]
    assert list(brand.products.all()) == [product]
    assert list(product.variants.all()) == [variant, other]
    assert product.description == ""
    assert product.is_active is True
    assert product.created_at is not None
    assert product.updated_at is not None


@pytest.mark.parametrize("field", ["price", "base_price", "from_price"])
def test_product_has_no_independent_price_field(field):
    """Price ownership remains on Variant, not a second Product column."""
    assert field not in {item.name for item in Product._meta.fields}


@pytest.mark.parametrize("amount", ["0.00", "89.90", "99999999.99"])
def test_decimal_price_round_trip_including_zero(amount, variant):
    """Valid decimal money and the chosen boundary survive DB round-trip."""
    variant.price = Decimal(amount)
    variant.full_clean()
    variant.save()
    variant.refresh_from_db()
    assert isinstance(variant.price, Decimal)
    assert variant.price == Decimal(amount)


def test_negative_price_model_validation(variant):
    """MinValueValidator returns a field error before saving a negative price."""
    variant.price = Decimal("-0.01")
    with pytest.raises(ValidationError) as exc:
        variant.full_clean()
    assert "price" in exc.value.message_dict
    assert ProductVariant.objects.get(pk=variant.pk).price == Decimal("12.50")


def test_negative_price_database_check(variant):
    """SQL rejects a negative cent even when model validation is bypassed."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductVariant.objects.filter(pk=variant.pk).update(price=Decimal("-0.01"))
    variant.refresh_from_db()
    assert variant.price == Decimal("12.50")


@pytest.mark.parametrize("amount", ["12.345", "100000000.00"])
def test_money_precision_is_model_validation(amount, variant):
    """Decimal precision/length are validated without claiming identical SQL rules."""
    variant.price = Decimal(amount)
    with pytest.raises(ValidationError) as exc:
        variant.full_clean()
    assert "price" in exc.value.message_dict
    assert ProductVariant.objects.get(pk=variant.pk).price == Decimal("12.50")


@pytest.mark.parametrize("field", ["sku", "barcode"])
def test_sku_and_filled_barcode_are_globally_unique(field, product, variant):
    """Uniqueness applies across products, not only inside one Product."""
    variant.barcode = "5901234123457"
    variant.save(update_fields=["barcode"])
    other_product = Product.objects.create(
        category=product.category, brand=product.brand, name="Other", slug="other"
    )
    payload = {
        "product": other_product,
        "sku": variant.sku if field == "sku" else "OTHER-01",
        "barcode": variant.barcode if field == "barcode" else None,
        "price": Decimal("1.00"),
    }
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductVariant.objects.create(**payload)
    assert ProductVariant.objects.count() == 1


def test_multiple_variants_without_barcode_are_allowed(product, variant):
    """Absent barcode uses NULL and does not conflict with another absent value."""
    other = ProductVariant.objects.create(
        product=product, sku="OTHER-01", price=Decimal("1.00")
    )
    variant.refresh_from_db()
    other.refresh_from_db()
    assert variant.barcode is None
    assert other.barcode is None
    assert ProductVariant.objects.filter(barcode__isnull=True).count() == 2


def test_barcode_preserves_leading_zeroes(variant):
    """Barcode is a string identifier, not an integer number."""
    variant.barcode = "00123456"
    variant.full_clean()
    variant.save()
    variant.refresh_from_db()
    assert variant.barcode == "00123456"


@pytest.mark.parametrize(
    "field,value",
    [
        ("sku", "lowercase"),
        ("sku", "BAD/SKU"),
        ("sku", "X" * 65),
        ("sku", ""),
        ("barcode", "abc"),
        ("barcode", "1" * 33),
        ("barcode", ""),
    ],
)
def test_variant_identifier_model_validation(field, value, variant):
    """Identifier format/length and canonical missing barcode are field errors."""
    setattr(variant, field, value)
    with pytest.raises(ValidationError) as exc:
        variant.full_clean()
    assert field in exc.value.message_dict


def test_empty_barcode_is_rejected_in_database(variant):
    """CHECK prevents an empty-string alternative to the documented NULL state."""
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductVariant.objects.filter(pk=variant.pk).update(barcode="")
    variant.refresh_from_db()
    assert variant.barcode is None


def test_product_slug_is_unique_even_when_inactive(product):
    """Deactivation does not release the Product slug in SQL."""
    product.is_active = False
    product.save(update_fields=["is_active"])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Product.objects.create(
                category=product.category,
                brand=product.brand,
                name="Duplicate",
                slug=product.slug,
            )
    assert Product.objects.count() == 1


def test_product_slug_namespace_is_separate_from_category(product, category):
    """A Product may share the literal slug of a different model."""
    product.slug = category.slug
    product.full_clean()
    product.save()
    product.refresh_from_db()
    assert product.slug == category.slug


@pytest.mark.parametrize("slug", ["Uppercase", "bad/slug", "x" * 101, ""])
def test_product_uses_existing_slug_validation(slug, product):
    """Product follows the current catalog slug format and maximum length."""
    product.slug = slug
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert "slug" in exc.value.message_dict


@pytest.mark.parametrize("quantity", [0, 7])
def test_inventory_quantity_and_reverse_relation(quantity, variant):
    """Zero/positive stock is valid and reverse access is a single object."""
    stock = Inventory(variant=variant, quantity=quantity)
    stock.full_clean()
    stock.save()
    variant.refresh_from_db()
    assert variant.inventory.pk == stock.pk
    assert variant.inventory.quantity == quantity
    assert stock.updated_at is not None


def test_inventory_default_is_zero_and_creation_is_explicit(variant):
    """OneToOne does not create stock; explicit creation defaults to zero."""
    assert Inventory.objects.count() == 0
    with pytest.raises(Inventory.DoesNotExist):
        _ = variant.inventory
    stock = Inventory.objects.create(variant=variant)
    assert stock.quantity == 0
    assert "reserved_quantity" not in {item.name for item in Inventory._meta.fields}


def test_inventory_is_unique_per_variant(variant):
    """SQL rejects a second stock row, preserving the original quantity."""
    stock = Inventory.objects.create(variant=variant, quantity=7)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Inventory.objects.create(variant=variant, quantity=99)
    stock.refresh_from_db()
    assert Inventory.objects.count() == 1
    assert stock.quantity == 7


def test_negative_inventory_model_validation(variant):
    """Negative quantity is rejected before SQL by model validation."""
    stock = Inventory(variant=variant, quantity=-1)
    with pytest.raises(ValidationError) as exc:
        stock.full_clean()
    assert "quantity" in exc.value.message_dict
    assert Inventory.objects.count() == 0


def test_negative_inventory_database_check(variant):
    """The named SQL CHECK also rejects an unvalidated negative update."""
    stock = Inventory.objects.create(variant=variant)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Inventory.objects.filter(pk=stock.pk).update(quantity=-1)
    stock.refresh_from_db()
    assert stock.quantity == 0


@pytest.mark.parametrize("value", ["18", "x" * 255])
def test_attribute_values_and_reverse_access(value, value_case, attribute):
    """Values use the shared type/unit and support the selected length boundary."""
    model, owner_name, owner = value_case
    item = model(**{owner_name: owner, "attribute": attribute, "value": value})
    item.full_clean()
    item.save()
    saved = owner.attribute_values.get()
    assert saved.value == value
    assert saved.attribute_id == attribute.pk
    assert saved.attribute.unit == "km/h"
    related = (
        attribute.product_values
        if owner_name == "product"
        else attribute.variant_values
    )
    assert related.get().pk == saved.pk


def test_attribute_pair_is_unique_in_database(value_case, attribute):
    """One owner/type pair cannot have two independent value rows."""
    model, owner_name, owner = value_case
    payload = {owner_name: owner, "attribute": attribute}
    original = model.objects.create(**payload, value="18")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            model.objects.create(**payload, value="20")
    original.refresh_from_db()
    assert model.objects.count() == 1
    assert original.value == "18"


def test_same_attribute_can_be_used_by_different_owners(value_case, attribute, product):
    """The pair is unique, not the Attribute globally across all owners."""
    model, owner_name, owner = value_case
    if owner_name == "product":
        other = Product.objects.create(
            category=product.category, brand=product.brand, name="Other", slug="other"
        )
    else:
        other = ProductVariant.objects.create(
            product=product, sku="OTHER-01", price=Decimal("1.00")
        )
    model.objects.create(**{owner_name: owner, "attribute": attribute, "value": "18"})
    model.objects.create(**{owner_name: other, "attribute": attribute, "value": "18"})
    assert model.objects.count() == 2


@pytest.mark.parametrize("value", ["", "x" * 256, None])
def test_attribute_value_model_validation(value, value_case, attribute):
    """Value is required and bounded, without pretending it is numeric typed data."""
    model, owner_name, owner = value_case
    item = model(**{owner_name: owner, "attribute": attribute, "value": value})
    with pytest.raises(ValidationError) as exc:
        item.full_clean()
    assert "value" in exc.value.message_dict
    assert model.objects.count() == 0


def test_shirt_variant_combinations_are_not_globally_unique(product):
    """Different SKUs may have identical Color/Size; no hidden combination rule."""
    color = Attribute.objects.create(name="Color", slug="color")
    size = Attribute.objects.create(name="Size", slug="size")
    for sku in ["BLACK-M-01", "BLACK-M-02"]:
        variant = ProductVariant.objects.create(
            product=product, sku=sku, price=Decimal("89.90")
        )
        VariantAttribute.objects.create(variant=variant, attribute=color, value="Black")
        VariantAttribute.objects.create(variant=variant, attribute=size, value="M")
    assert product.variants.count() == 2
    assert color.variant_values.count() == 2
    assert size.variant_values.count() == 2


@pytest.mark.parametrize(
    "model,field",
    [
        (Product, "category_id"),
        (Product, "brand_id"),
        (ProductVariant, "product_id"),
        (Inventory, "variant_id"),
        (ProductAttributeValue, "product_id"),
        (ProductAttributeValue, "attribute_id"),
        (VariantAttribute, "variant_id"),
        (VariantAttribute, "attribute_id"),
        (ProductImage, "product_id"),
    ],
)
@pytest.mark.parametrize("invalid_id", [None, 999999999])
def test_required_foreign_keys_in_database(
    model, field, invalid_id, product, variant, attribute
):
    """Each declared required relation has NOT NULL and a real FK in PostgreSQL."""
    payloads = {
        Product: {
            "category_id": product.category_id,
            "brand_id": product.brand_id,
            "name": "New",
            "slug": "new-product",
        },
        ProductVariant: {
            "product_id": product.pk,
            "sku": "NEW-01",
            "price": Decimal("1.00"),
        },
        Inventory: {"variant_id": variant.pk},
        ProductAttributeValue: {
            "product_id": product.pk,
            "attribute_id": attribute.pk,
            "value": "18",
        },
        VariantAttribute: {
            "variant_id": variant.pk,
            "attribute_id": attribute.pk,
            "value": "18",
        },
        ProductImage: {
            "product_id": product.pk,
            "image_url": "https://images.example.com/products/fk-test.jpg",
        },
    }
    payload = {**payloads[model], field: invalid_id}
    before = model.objects.count()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            model.objects.create(**payload)
            connection.check_constraints()
    assert model.objects.count() == before


@pytest.mark.parametrize(
    "url",
    [
        "https://images.example.com/products/front.jpg",
        "https://images.example.com/"
        + "x" * (2048 - len("https://images.example.com/")),
    ],
    ids=["usual", "max-length"],
)
def test_image_url_round_trip(url, product):
    """Valid URLs, including the length boundary, round-trip without a live check."""
    photo = ProductImage(product=product, image_url=url, alt_text="Front")
    photo.full_clean()
    photo.save()
    photo.refresh_from_db()
    assert photo.product_id == product.pk
    assert product.images.get().pk == photo.pk
    assert photo.image_url == url
    assert isinstance(photo.image_url, str)
    assert photo.is_primary is False
    assert photo.position == 0
    assert photo.alt_text == "Front"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not-a-url",
        "/products/relative.jpg",
        "https://images.example.com/" + "x" * 2048,
        None,
    ],
)
def test_image_url_model_validation(url, product):
    """Empty, malformed, relative, overlong and null URLs fail before saving."""
    photo = ProductImage(product=product, image_url=url)
    with pytest.raises(ValidationError) as exc:
        photo.full_clean()
    assert "image_url" in exc.value.message_dict
    assert ProductImage.objects.count() == 0


def test_image_ordering_allows_equal_positions(product):
    """Position ties are valid and deterministically resolved by ID."""
    last = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/products/last.jpg",
        position=2,
    )
    first = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/products/first.jpg",
        position=1,
    )
    second = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/products/second.jpg",
        position=1,
    )
    assert list(product.images.values_list("id", flat=True)) == [
        first.pk,
        second.pk,
        last.pk,
    ]


def test_no_images_or_no_primary_is_allowed(product):
    """The model requires neither an image nor an automatically chosen primary."""
    assert product.images.count() == 0
    for name in ["a.jpg", "b.jpg"]:
        ProductImage.objects.create(
            product=product, image_url=f"https://images.example.com/products/{name}"
        )
    assert product.images.count() == 2
    assert product.images.filter(is_primary=True).count() == 0


def test_only_one_primary_image_per_product(product):
    """The partial unique index rejects a second primary without replacing the first."""
    original = ProductImage.objects.create(
        product=product,
        image_url="https://images.example.com/products/first.jpg",
        is_primary=True,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductImage.objects.create(
                product=product,
                image_url="https://images.example.com/products/second.jpg",
                is_primary=True,
            )
    original.refresh_from_db()
    assert original.is_primary is True
    assert product.images.count() == 1


def test_different_products_can_each_have_primary_image(product):
    """Primary uniqueness is per Product, not a global single image."""
    other = Product.objects.create(
        category=product.category, brand=product.brand, name="Other", slug="other"
    )
    for owner in [product, other]:
        ProductImage.objects.create(
            product=owner,
            image_url="https://images.example.com/products/front.jpg",
            is_primary=True,
        )
    assert ProductImage.objects.filter(is_primary=True).count() == 2


def test_negative_image_position_validation_and_sql(product):
    """Both validation and CHECK enforce the chosen nonnegative ordering index."""
    photo = ProductImage(
        product=product,
        image_url="https://images.example.com/products/front.jpg",
        position=-1,
    )
    with pytest.raises(ValidationError) as exc:
        photo.full_clean()
    assert "position" in exc.value.message_dict
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProductImage.objects.create(
                product=product,
                image_url="https://images.example.com/products/front.jpg",
                position=-1,
            )
    assert ProductImage.objects.count() == 0


@pytest.mark.parametrize(
    "target", ["category", "brand", "product", "product_attribute", "variant_attribute"]
)
def test_protect_preserves_in_use_references(target, product, variant, attribute):
    """Used reference types and Product with variants are protected from ORM deletion."""
    if target == "product_attribute":
        ProductAttributeValue.objects.create(
            product=product, attribute=attribute, value="18"
        )
    elif target == "variant_attribute":
        VariantAttribute.objects.create(
            variant=variant, attribute=attribute, value="18"
        )
    obj = {
        "category": product.category,
        "brand": product.brand,
        "product": product,
        "product_attribute": attribute,
        "variant_attribute": attribute,
    }[target]
    with pytest.raises(ProtectedError):
        obj.delete()
    assert Product.objects.filter(pk=product.pk).exists()
    assert ProductVariant.objects.filter(pk=variant.pk).exists()
    assert Attribute.objects.filter(pk=attribute.pk).exists()


def test_deleting_variant_cascades_stock_and_values(product, variant, attribute):
    """Chosen child CASCADE removes stock/variant values, not the Product."""
    Inventory.objects.create(variant=variant, quantity=7)
    VariantAttribute.objects.create(variant=variant, attribute=attribute, value="18")
    variant_id = variant.pk
    variant.delete()
    assert not ProductVariant.objects.filter(pk=variant_id).exists()
    assert Inventory.objects.count() == 0
    assert VariantAttribute.objects.count() == 0
    assert Product.objects.filter(pk=product.pk).exists()
    assert Attribute.objects.filter(pk=attribute.pk).exists()


def test_deleting_product_without_variants_cascades_its_child_rows(product, attribute):
    """An unused Product may be removed with its values/image rows, retaining types."""
    ProductAttributeValue.objects.create(
        product=product, attribute=attribute, value="18"
    )
    ProductImage.objects.create(
        product=product, image_url="https://images.example.com/products/front.jpg"
    )
    product.delete()
    assert Product.objects.count() == 0
    assert ProductAttributeValue.objects.count() == 0
    assert ProductImage.objects.count() == 0
    assert Attribute.objects.filter(pk=attribute.pk).exists()


def test_deactivation_preserves_all_links_and_stock(product, variant, attribute):
    """Soft deactivation changes flags only, without cascading to variants or children."""
    stock = Inventory.objects.create(variant=variant, quantity=7)
    ProductAttributeValue.objects.create(
        product=product, attribute=attribute, value="18"
    )
    VariantAttribute.objects.create(variant=variant, attribute=attribute, value="18")
    ProductImage.objects.create(
        product=product, image_url="https://images.example.com/products/front.jpg"
    )
    product.is_active = False
    product.save(update_fields=["is_active", "updated_at"])
    variant.refresh_from_db()
    assert variant.is_active is True
    assert variant.product_id == product.pk
    variant.is_active = False
    variant.save(update_fields=["is_active"])
    stock.refresh_from_db()
    assert stock.variant_id == variant.pk
    assert stock.quantity == 7
    assert product.attribute_values.count() == 1
    assert variant.attribute_values.count() == 1
    assert product.images.count() == 1