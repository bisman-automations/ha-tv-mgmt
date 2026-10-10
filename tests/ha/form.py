"""Serialize a form's schema the way Home Assistant sends it to the frontend."""

from homeassistant.helpers import config_validation as cv


def fields(schema):
    try:  # Home Assistant 2026.9 and newer
        import probatio

        return probatio.to_field_list(schema, custom_serializer=cv.custom_serializer)
    except ImportError:
        import voluptuous_serialize

        return voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)
