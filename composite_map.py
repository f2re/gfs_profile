import math


def area_box_from_radius(lat, lon, radius_km):
    dlat = radius_km / 110.574
    cos_lat = max(0.08, abs(math.cos(math.radians(lat))))
    dlon = radius_km / (111.320 * cos_lat)
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon
