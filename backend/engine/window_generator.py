"""
Window Generator for VoxAssist Floor Plans.
Places windows on exterior walls based on room type and architectural conventions.
"""
import random
from shapely.geometry import Polygon, LineString, MultiPolygon, box
from shapely.ops import unary_union
from collections import defaultdict

# Window dimensions in meters
WINDOW_CONFIGS = {
    "living":   {"width": 1.5, "height": 1.2, "sill_height": 0.9, "count": (2, 3)},
    "bedroom":  {"width": 1.2, "height": 1.0, "sill_height": 0.9, "count": (1, 2)},
    "kitchen":  {"width": 1.0, "height": 0.8, "sill_height": 1.0, "count": (1, 1)},
    "dining":   {"width": 1.4, "height": 1.2, "sill_height": 0.9, "count": (1, 2)},
    "study":    {"width": 1.2, "height": 1.0, "sill_height": 0.9, "count": (1, 2)},
    "bathroom": {"width": 0.6, "height": 0.4, "sill_height": 1.8, "count": (1, 1)},
    "storage":  {"width": 0.6, "height": 0.6, "sill_height": 1.5, "count": (0, 1)},
    "hallway":  {"width": 0.8, "height": 0.8, "sill_height": 1.2, "count": (0, 1)},
    "balcony":  {"width": 0.0, "height": 0.0, "sill_height": 0.0, "count": (0, 0)},
}

DEFAULT_CONFIG = {"width": 1.0, "height": 0.8, "sill_height": 1.0, "count": (1, 1)}

# Colors
WINDOW_FRAME_COLOR = "#5D4037"   # Dark wood
WINDOW_GLASS_COLOR = "#87CEEB"   # Sky blue


def _get_room_type(room_name):
    return room_name.split("_")[0].lower()


def _find_exterior_walls(rooms):
    """
    Find wall segments that are truly on the exterior (not shared with any other room).
    Uses geometric verification: a point offset outward from the wall midpoint
    must lie outside ALL other room polygons.
    """
    from shapely.geometry import Point
    
    all_other_polys = {}
    for name, poly in rooms.items():
        if not poly.is_empty:
            all_other_polys[name] = poly
    
    exterior_walls = []
    
    for room_name, poly in rooms.items():
        if poly.is_empty:
            continue
        
        coords = list(poly.exterior.coords)
        room_centroid = poly.centroid
        
        for i in range(len(coords) - 1):
            p1 = coords[i]
            p2 = coords[i + 1]
            
            seg = LineString([p1, p2])
            if seg.length < 0.8:  # Minimum wall length for a window
                continue
            
            # Compute outward normal from this room's perspective
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            length = (dx**2 + dy**2)**0.5
            if length < 0.1:
                continue
            
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux  # perpendicular
            
            # Midpoint of segment
            mx = (p1[0] + p2[0]) / 2
            my = (p1[1] + p2[1]) / 2
            
            # Ensure normal points outward
            to_center_x = room_centroid.x - mx
            to_center_y = room_centroid.y - my
            dot = nx * to_center_x + ny * to_center_y
            if dot > 0:
                nx, ny = -nx, -ny
            
            # Test point slightly outside the wall
            test_offset = 0.15
            test_point = Point(mx + nx * test_offset, my + ny * test_offset)
            
            # Check if this point is inside ANY other room
            is_interior = False
            for other_name, other_poly in all_other_polys.items():
                if other_name == room_name:
                    continue
                if other_poly.contains(test_point) or other_poly.boundary.distance(test_point) < 0.05:
                    is_interior = True
                    break
            
            if not is_interior:
                exterior_walls.append((room_name, seg))
    
    return exterior_walls


def generate_windows(rooms, doors_geom=None, entrance_geom=None):
    """
    Generate window placements on exterior walls.
    
    Args:
        rooms: dict of {room_name: Polygon}
        doors_geom: Polygon/MultiPolygon of door geometry (to avoid overlap)
        entrance_geom: Polygon of entrance door (to avoid overlap with larger buffer)
    
    Returns:
        list of dicts, each with:
            - 'room': room name
            - 'position': (x, y) center of window on wall
            - 'width': window width
            - 'height': window height
            - 'sill_height': height from floor to window bottom
            - 'wall_segment': LineString of the wall the window is on
            - 'normal': (nx, ny) outward-facing normal direction
    """
    if not rooms:
        return []
    
    # Get exterior walls grouped by room
    exterior_walls = _find_exterior_walls(rooms)
    
    # Group by room
    room_ext_walls = defaultdict(list)
    for room_name, wall_line in exterior_walls:
        room_ext_walls[room_name].append(wall_line)
    
    # Collect door geometry for avoiding overlap
    door_polys = []
    if doors_geom:
        if isinstance(doors_geom, Polygon):
            door_polys.append(doors_geom.buffer(0.3))
        elif isinstance(doors_geom, MultiPolygon):
            door_polys.extend([d.buffer(0.3) for d in doors_geom.geoms])
    
    # Entrance door gets a LARGER exclusion zone (1.0m buffer)
    if entrance_geom and isinstance(entrance_geom, Polygon) and not entrance_geom.is_empty:
        door_polys.append(entrance_geom.buffer(1.5))
    
    windows = []
    
    for room_name, walls in room_ext_walls.items():
        rtype = _get_room_type(room_name)
        config = WINDOW_CONFIGS.get(rtype, DEFAULT_CONFIG)
        room_poly = rooms[room_name]
        room_cx, room_cy = room_poly.centroid.x, room_poly.centroid.y
        
        min_count, max_count = config["count"]
        if max_count == 0:
            continue
        
        target_count = random.randint(min_count, max_count)
        if target_count == 0:
            continue
        
        # Sort walls by length (prefer longer walls for windows)
        walls_sorted = sorted(walls, key=lambda w: w.length, reverse=True)
        
        placed = 0
        for wall in walls_sorted:
            if placed >= target_count:
                break
            
            wall_length = wall.length
            win_width = config["width"]
            
            # Need at least window width + 0.3m margin on each side
            if wall_length < win_width + 0.6:
                continue
            
            # Place window centered on wall
            wall_coords = list(wall.coords)
            x1, y1 = wall_coords[0]
            x2, y2 = wall_coords[-1]
            
            # Direction along wall
            dx = x2 - x1
            dy = y2 - y1
            length = (dx**2 + dy**2)**0.5
            if length < 0.1:
                continue
            ux, uy = dx / length, dy / length  # Unit direction along wall
            nx, ny = -uy, ux  # Perpendicular
            
            # Window center position (centered on wall)
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            
            # ENSURE normal points OUTWARD (away from room center)
            to_center_x = room_cx - cx
            to_center_y = room_cy - cy
            dot = nx * to_center_x + ny * to_center_y
            if dot > 0:
                # Normal points toward center — flip it
                nx, ny = -nx, -ny
            
            # Check if window position overlaps with a door
            win_point = Polygon([
                (cx - ux * win_width / 2, cy - uy * win_width / 2),
                (cx + ux * win_width / 2, cy + uy * win_width / 2),
                (cx + ux * win_width / 2 + nx * 0.1, cy + uy * win_width / 2 + ny * 0.1),
                (cx - ux * win_width / 2 + nx * 0.1, cy - uy * win_width / 2 + ny * 0.1),
            ])
            
            overlaps_door = any(win_point.intersects(dp) for dp in door_polys)
            if overlaps_door:
                continue
            
            windows.append({
                "room": room_name,
                "position": (cx, cy),
                "width": win_width,
                "height": config["height"],
                "sill_height": config["sill_height"],
                "wall_segment": wall,
                "normal": (nx, ny),
                "wall_dir": (ux, uy),
            })
            placed += 1
    
    return windows
