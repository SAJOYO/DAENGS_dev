def encode(points: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10**precision
    output: list[str] = []
    previous_latitude = previous_longitude = 0
    for latitude, longitude in points:
        integer_latitude = round(latitude * factor)
        integer_longitude = round(longitude * factor)
        for value in (
            integer_latitude - previous_latitude,
            integer_longitude - previous_longitude,
        ):
            value = ~(value << 1) if value < 0 else value << 1
            while value >= 0x20:
                output.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            output.append(chr(value + 63))
        previous_latitude = integer_latitude
        previous_longitude = integer_longitude
    return "".join(output)
