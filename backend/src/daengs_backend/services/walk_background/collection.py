"""Choose a source for the original record/pin; no database or provider parsing."""

from daengs_backend.services.walk_background.contracts import Collected


async def collect(tag, content, *, client=None):
    pin = content.get("pin")
    if pin and pin["state"] == "provisional":
        return Collected("not_requested", "pin_not_final")
    point = pin.get("point") if pin else content.get("location")
    if point is None:
        return Collected("not_requested", "no_location")
    if tag == "environment.weather":
        from daengs_backend.services.walk_background.providers.weather import collect_temperature

        return await collect_temperature(content, point)
    if tag in {"space.address", "space.park", "space.commerce", "space.river"}:
        from daengs_backend.services.walk_background.providers.public import collect_public

        return await collect_public(tag, point, pin)
    if tag != "space.facility":
        return Collected("not_requested", "provider_not_connected")
    from daengs_backend.services.walk_background.providers.facility import collect_facility

    return await collect_facility(point, pin, client=client)
