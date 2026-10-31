MAX_CART_QUANTITY = 999


def variant_is_active(variant):
    product = variant.product
    return (
        variant.is_active
        and product.is_active
        and product.category.is_active
        and product.brand.is_active
    )
