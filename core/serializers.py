from typing import Mapping

from rest_framework import serializers


class StrictInputMixin:
    """
    Enforce strict field validation on a DRF serializer.

    DRF normally ignores unknown and read-only fields. This mixin rejects
    submitted fields of either kind with ValidationError.

    List this mixin before Serializer or ModelSerializer in the base classes.
    """

    def to_internal_value(self, data):
        """
        Check incoming fields before DRF validation.
        """
        if isinstance(data, Mapping):
            writable = {
                name for name, field in self.fields.items() if not field.read_only
            }

            forbidden = set(data) - writable

            if forbidden:
                raise serializers.ValidationError(
                    {
                        name: ["This field cannot be submitted."]
                        for name in sorted(forbidden)
                    }
                )

        return super().to_internal_value(data)
