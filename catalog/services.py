from django.db import IntegrityError, connection, transaction
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404

from .models import Inventory, Product, ProductImage, ProductVariant
from .storage import cleanup_object, get_storage


def _unique_conflict(exc, model, allowed_fields):
    cause = exc.__cause__
    if getattr(cause, "sqlstate", None) != "23505":
        raise exc
    name = getattr(getattr(cause, "diag", None), "constraint_name", None)
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor, model._meta.db_table
        )
    constraint = constraints.get(name, {})
    columns = constraint.get("columns", [])
    if constraint.get("unique") and len(columns) == 1 and columns[0] in allowed_fields:
        raise ValidationError({columns[0]: ["Already exists."]}) from exc
    raise exc


def create_product(**data):
    try:
        with transaction.atomic():
            return Product.objects.create(**data)
    except IntegrityError as exc:
        _unique_conflict(exc, Product, {"slug"})


def update_product(product_id, **data):
    try:
        with transaction.atomic():
            product = get_object_or_404(
                Product.objects.select_for_update(), pk=product_id
            )
            if "slug" in data and data["slug"] != product.slug:
                raise ValidationError({"slug": ["Slug cannot be changed."]})
            for field, value in data.items():
                setattr(product, field, value)
            product.save()
            return product
    except IntegrityError as exc:
        _unique_conflict(exc, Product, {"slug"})


def create_variant(product_id, **data):
    try:
        with transaction.atomic():
            product = get_object_or_404(Product, pk=product_id)
            variant = ProductVariant.objects.create(product=product, **data)
            Inventory.objects.create(variant=variant, quantity=0)
            return variant
    except IntegrityError as exc:
        _unique_conflict(exc, ProductVariant, {"sku", "barcode"})


def update_variant(product_id, variant_id, **data):
    try:
        with transaction.atomic():
            variant = get_object_or_404(
                ProductVariant.objects.select_for_update(),
                pk=variant_id,
                product_id=product_id,
            )
            if "sku" in data and data["sku"] != variant.sku:
                raise ValidationError({"sku": ["SKU cannot be changed."]})
            for field, value in data.items():
                setattr(variant, field, value)
            variant.save()
            return variant
    except IntegrityError as exc:
        _unique_conflict(exc, ProductVariant, {"sku", "barcode"})


def update_inventory(product_id, variant_id, *, quantity):
    with transaction.atomic():
        variant = get_object_or_404(
            ProductVariant.objects.select_for_update(),
            pk=variant_id,
            product_id=product_id,
        )
        inventory, _ = Inventory.objects.get_or_create(variant=variant)
        inventory.quantity = quantity
        inventory.save(update_fields=["quantity", "updated_at"])
        return inventory


def _image_boundary():
    if not transaction.get_autocommit():
        raise RuntimeError("Image operations require an outer autocommit boundary.")


def create_image(product_id, *, file, **metadata):
    _image_boundary()
    get_object_or_404(Product, pk=product_id)
    storage = get_storage()
    uploaded = storage.upload(file)
    try:
        with transaction.atomic(durable=True):
            product = get_object_or_404(
                Product.objects.select_for_update(), pk=product_id
            )
            if metadata.get("is_primary", False):
                ProductImage.objects.filter(product=product, is_primary=True).update(
                    is_primary=False
                )
            image = ProductImage.objects.create(
                product=product,
                image_url=uploaded.url,
                storage_key=uploaded.key,
                **metadata,
            )
        return image
    except Exception:
        cleanup_object(storage, uploaded.key)
        raise


def update_image(product_id, image_id, *, file=None, **metadata):
    _image_boundary()
    get_object_or_404(ProductImage, pk=image_id, product_id=product_id)
    storage = get_storage() if file is not None else None
    uploaded = storage.upload(file) if file is not None else None
    try:
        with transaction.atomic(durable=True):
            get_object_or_404(Product.objects.select_for_update(), pk=product_id)
            image = get_object_or_404(
                ProductImage.objects.select_for_update(),
                pk=image_id,
                product_id=product_id,
            )
            old_key = image.storage_key

            if metadata.get("is_primary", False):
                ProductImage.objects.filter(
                    product_id=product_id, is_primary=True
                ).exclude(pk=image_id).update(is_primary=False)

            for field, value in metadata.items():
                setattr(image, field, value)

            if uploaded is not None:
                image.image_url = uploaded.url
                image.storage_key = uploaded.key

            image.save()

            if uploaded is not None and old_key:
                transaction.on_commit(
                    lambda: cleanup_object(storage, old_key), robust=True
                )

        return image

    except Exception:
        if uploaded is not None:
            cleanup_object(storage, uploaded.key)
        raise


def delete_image(product_id, image_id):
    _image_boundary()
    with transaction.atomic(durable=True):
        get_object_or_404(Product.objects.select_for_update(), pk=product_id)
        image = get_object_or_404(
            ProductImage.objects.select_for_update(), pk=image_id, product_id=product_id
        )
        key = image.storage_key
        image.delete()
        if key:
            storage = get_storage()
            transaction.on_commit(lambda: cleanup_object(storage, key), robust=True)
