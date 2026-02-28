from shapely.geometry import box, Point, Polygon, LineString
from shapely.ops import unary_union
import numpy as np
import random

from adjacency_rules import ADJACENCY_RULES, validate_adjacency
from corridor_generator import generate_corridors
from door_generator import generate_doors

# =========================
# CONSTANTS
# =========================
# Opening widths in meters (wider so gaps read clearly in 2D/3D)
DOOR_WIDTH = 1.4
OPEN_SPACE_WIDTH = 3.2
WALL_TOLERANCE = 0.5
JITTER = 0.15  # Small positional noise

# =========================
# DEFAULT CONFIG
# =========================
DEFAULT_CONFIG = {
    "GAP": 0.0,
    "EXTERNAL_OFFSET": 5.0,
    "MAX_ROW_WIDTH": 40.0,
    "RANDOM_SEED": None,
}

ZONES = {
    "public": ["living", "dining"],
    "semi_public": ["kitchen"],
    "private": ["bedroom", "study"],
    "service": ["bathroom", "storage", "utility"],
}

def get_zone(room_type):
    for zone, types in ZONES.items():
        if room_type in types:
            return zone
    return "other"

def _random_aspect_ratio(base_ratio=1.5, variance=0.5):
    min_ratio = max(0.8, base_ratio - variance)
    max_ratio = min(2.0, base_ratio + variance)
    return random.uniform(min_ratio, max_ratio)

def _place_adjacent(base_poly, width, height, existing_polys, preferred_sides=None):
    """
    Place room adjacent to base_poly.
    Priority:
    1. Matches preferred_sides (if provided).
    2. MAXIMIZES shared perimeter with ALL existing polys (Gap Filling / Corner Logic).
    3. Respects maximum footprint bounds (prevents sprawling layouts).
    """
    # Safety check: prevent infinite sprawl
    MAX_DIMENSION = 100.0  # Maximum room dimension in meters
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        return None
    
    minx, miny, maxx, maxy = base_poly.bounds
    
    all_placements = {
        'right': box(maxx, miny, maxx + width, miny + height),
        'left': box(minx - width, miny, minx, miny + height),
        'top': box(minx, maxy, minx + width, maxy + height),
        'bottom': box(minx, miny - height, minx + width, miny),
    }
    
    # 1. Filter by Preference
    if preferred_sides:
        candidates = [all_placements[s] for s in preferred_sides if s in all_placements]
        # Add non-preferred as fallback (lower priority? For now just mix them in if needed, but let's stick to strict preference first)
        # Actually, if we are smart about scoring, we can consider ALL valid placements and just give a bonus to preferred sides.
        # Let's try to stick to the requested preference strictness first, then fallback.
    else:
        candidates = list(all_placements.values())
        
    # If no preferred candidates logic (simplified above), just use all if preference failed?
    # No, the previous logic fell back to others. Let's do:
    primary_candidates = []
    secondary_candidates = []
    
    for s, p in all_placements.items():
        if preferred_sides and s in preferred_sides:
            primary_candidates.append(p)
        else:
            secondary_candidates.append(p)
            
    # Try per-candidate validation
    valid_candidates = []
    
    # Helper to check validity and score
    def evaluate_candidate(cand, loops_list):
        # 1. Check Overlaps
        for existing in loops_list:
            if cand.intersects(existing):
                if cand.intersection(existing).area > 1e-6:
                    return None # Invalid (Overlap)
        
        # 2. Calculate Contact Score (Shared Perimeter)
        contact_length = 0
        cand_boundary = cand.boundary
        for existing in loops_list:
            if cand.touches(existing) or cand.intersects(existing): # Intersects handles flush edges too
                 intersection = cand.intersection(existing)
                 # If intersection is line (touching), add length
                 # Intersection of boxes is often a box or line
                 # Actually `intersection` of two adjacent boxes is a LineString.
                 common = cand_boundary.intersection(existing.boundary)
                 if not common.is_empty:
                     contact_length += common.length
        return contact_length

    # Check Primary
    from shapely.geometry import Polygon
    # existing_polys is a dict_values or list. Convert to list for iteration.
    poly_list = list(existing_polys)
    
    for cand in primary_candidates:
        score = evaluate_candidate(cand, poly_list)
        if score is not None:
            valid_candidates.append((cand, score + 100)) # Bonus for preferred
            
    # Check Secondary (Fallback)
    if not valid_candidates:
        for cand in secondary_candidates:
            score = evaluate_candidate(cand, poly_list)
            if score is not None:
                valid_candidates.append((cand, score))
    
    if not valid_candidates:
        return None
        
    # Sort by Contact Score (Descending)
    # This favors "Corner Filling" (touching 2 sides > touching 1 side)
    valid_candidates.sort(key=lambda x: x[1], reverse=True)
    
    # Pick top 1 (Deterministically best fit) or Weighted random?
    # Deterministic encourages compactness.
    return valid_candidates[0][0]

def _get_external_walls(poly, all_other_polys):
    boundary = poly.boundary
    if all_other_polys:
        other_boundaries = unary_union([p.boundary for p in all_other_polys if not p.is_empty])
    else:
        other_boundaries = Polygon()
    external = boundary.difference(other_boundaries.buffer(1e-6))
    
    segments = []
    if external.is_empty:
        return segments
    if isinstance(external, LineString):
        if external.length > WALL_TOLERANCE:
            segments.append(external)
    elif hasattr(external, 'geoms'):
        for geom in external.geoms:
            if isinstance(geom, LineString) and geom.length > WALL_TOLERANCE:
                segments.append(geom)
    return segments

def _generate_entrance_door(living_room_poly, all_rooms):
    other_rooms = [p for name, p in all_rooms.items() if name != next(k for k in all_rooms if k.startswith("living"))]
    external_walls = _get_external_walls(living_room_poly, other_rooms)
    
    if not external_walls:
        return None
    
    valid_walls = [w for w in external_walls if w.length >= DOOR_WIDTH]
    if not valid_walls:
        return None
    
    valid_walls.sort(key=lambda w: w.length, reverse=True)
    best_wall = random.choice(valid_walls[:3])
    t = random.uniform(0.2, 0.8)
    mid_point = best_wall.interpolate(t, normalized=True)
    
    coords = list(best_wall.coords)
    if len(coords) < 2:
        return None
    x1, y1 = coords[0]
    x2, y2 = coords[-1]
    dx, dy = x2 - x1, y2 - y1
    length = (dx**2 + dy**2)**0.5
    if length == 0:
        return None
    
    nx, ny = dx / length, dy / length
    px, py = -ny, nx
    
    door_depth = 0.3
    w = DOOR_WIDTH / 2
    d = door_depth / 2
    
    return Polygon([
        (mid_point.x - nx*w - px*d, mid_point.y - ny*w - py*d),
        (mid_point.x + nx*w - px*d, mid_point.y + ny*w - py*d),
        (mid_point.x + nx*w + px*d, mid_point.y + ny*w + py*d),
        (mid_point.x - nx*w + px*d, mid_point.y - ny*w + py*d),
    ])

def _determine_opening_width(r1, r2):
    t1 = r1.split("_")[0]
    t2 = r2.split("_")[0]
    if {t1, t2} == {"living", "kitchen"}:
        return OPEN_SPACE_WIDTH
    return DOOR_WIDTH

def _get_compact_sides(existing_layouts):
    """
    Determine preferred placement sides to maintain a compact (square-ish) footprint.
    Returns list of sides ['top', 'bottom',...] sorted by preference.
    """
    if not existing_layouts:
        return ['right', 'left', 'top', 'bottom']
        
    # Calculate current bounding box
    minx, miny, maxx, maxy = float('inf'), float('inf'), float('-inf'), float('-inf')
    for poly in existing_layouts.values():
        x0, y0, x1, y1 = poly.bounds
        minx = min(minx, x0)
        miny = min(miny, y0)
        maxx = max(maxx, x1)
        maxy = max(maxy, y1)
        
    width = maxx - minx
    height = maxy - miny
    
    # Heuristic: If Wide, stack Top/Bottom. If Tall, stack Left/Right.
    # Aspect Ratio > 1.2 (Wide) -> Prefer Vertical
    # Aspect Ratio < 0.8 (Tall) -> Prefer Horizontal
    aspect = width / (height + 1e-6)
    
    sides = ['top', 'bottom', 'right', 'left']
    
    if aspect > 1.2:
        return ['top', 'bottom', 'right', 'left']
    elif aspect < 0.8:
        return ['right', 'left', 'top', 'bottom']
    else:
        random.shuffle(sides)
        return sides

def synthesize_single_floor(spec, config=None):
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    if cfg.get("RANDOM_SEED") is not None:
        random.seed(cfg["RANDOM_SEED"])
        np.random.seed(cfg["RANDOM_SEED"])
    
    if not spec.get("rooms"):
        raise ValueError("Spec must contain non-empty 'rooms' list")
    
    rooms_by_zone = {k: [] for k in ZONES.keys()}
    rooms_by_zone["other"] = []
    
    for i, room in enumerate(spec["rooms"]):
        r_type = room.get("type")
        area = room.get("area")
        if not r_type or not isinstance(r_type, str):
            raise ValueError(f"Room {i} missing or invalid 'type'")
        if not (isinstance(area, (int, float)) and area > 0):
            raise ValueError(f"Room {i} has invalid area: {area}")
        zone = get_zone(r_type)
        rooms_by_zone[zone].append(room)
    
    layouts = {}
    room_index = {}
    
    print("\n🏗️  Building house with architectural zones:")
    
    # PHASE 1: CORE (LIVING)
    living_rooms = rooms_by_zone["public"]
    core_room = None
    if living_rooms:
        # 1. Primary Living Room (The Hub)
        room = living_rooms[0]
        r_type = room["type"]
        area = float(room["area"])
        # High Variance for Core
        base_ar = random.choice([1.2, 1.5, 1.8, 0.8])
        aspect_ratio = _random_aspect_ratio(base_ar, 0.5)
        height = (area / aspect_ratio) ** 0.5
        width = aspect_ratio * height
        room_index[r_type] = 1
        room_name = f"{r_type}_1"
        poly = box(0, 0, width, height)
        layouts[room_name] = poly
        core_room = room_name
        print(f"  🏠 CORE: {room_name} (hub)")
        
        core_poly = layouts[core_room]

        # 2. Additional Public Rooms (e.g. Dining)
        for room in living_rooms[1:]:
            r_type = room["type"]
            area = float(room["area"])
            base_ar = random.choice([1.1, 1.4, 1.0])
            aspect_ratio = _random_aspect_ratio(base_ar, 0.4)
            height = (area / aspect_ratio) ** 0.5
            width = aspect_ratio * height
            room_index[r_type] = room_index.get(r_type, 0) + 1
            room_name = f"{r_type}_{room_index[r_type]}"
            
            # Place adjacent to core
            preferred_sides = _get_compact_sides(layouts)
            poly = _place_adjacent(core_poly, width, height, layouts.values(), preferred_sides)
            
            if poly:
                layouts[room_name] = poly
                print(f"  🍽️  DINING/PUBLIC: {room_name} (attached to core)")
    
    if not core_room:
        raise ValueError("No living room defined")
    # core_poly is already defined above
    
    # PHASE 2: KITCHEN
    kitchen_rooms = rooms_by_zone["semi_public"]
    for room in kitchen_rooms:
        r_type = room["type"]
        area = float(room["area"])
        base_ar = random.choice([1.0, 1.3, 0.9])
        aspect_ratio = _random_aspect_ratio(base_ar, 0.3)
        height = (area / aspect_ratio) ** 0.5
        width = aspect_ratio * height
        room_index[r_type] = room_index.get(r_type, 0) + 1
        room_name = f"{r_type}_{room_index[r_type]}"
        
        # Priority: Attach to DINING if exists, else Core
        dining_rooms = [r for r in layouts.keys() if "dining" in r]
        
        poly = None
        if dining_rooms:
            # Try attaching to dining first (Living -> Dining -> Kitchen flow)
            for dining in dining_rooms:
                preferred_sides = _get_compact_sides(layouts)
                poly = _place_adjacent(layouts[dining], width, height, layouts.values(), preferred_sides)
                if poly:
                    print(f"  🍳 SEMI-PUBLIC: {room_name} (attached to {dining})")
                    break
        
        if not poly:
             # Fallback to Core
             preferred_sides = _get_compact_sides(layouts)
             poly = _place_adjacent(core_poly, width, height, layouts.values(), preferred_sides[:2])
             if poly:
                 print(f"  🍳 SEMI-PUBLIC: {room_name} (attached to core)")
        
        if poly:
            layouts[room_name] = poly

    
    # PHASE 3: BEDROOMS
    bedrooms = rooms_by_zone["private"]
    # Randomize order of bedrooms
    random.shuffle(bedrooms)
    bedroom_names = []
    for idx, room in enumerate(bedrooms):
        r_type = room["type"]
        area = float(room["area"])
        base_ar = random.choice([1.1, 1.4, 1.0])
        aspect_ratio = _random_aspect_ratio(base_ar, 0.4)
        height = (area / aspect_ratio) ** 0.5
        width = aspect_ratio * height
        room_index[r_type] = room_index.get(r_type, 0) + 1
        room_name = f"{r_type}_{room_index[r_type]}"
        
        # Use Compactness Bias
        preferred_sides = _get_compact_sides(layouts)
        
        # Prefer attaching to Core (Hall/Living) to ensure access
        # NOT attaching to other bedrooms to avoid daisy-chaining without doors
        poly = _place_adjacent(core_poly, width, height, layouts.values(), preferred_sides[:3])
        
        if not poly:
             # Try other public rooms (Dining?)
             public_rooms = [r for r in layouts.keys() if "dining" in r or "living" in r]
             for pub in public_rooms:
                 poly = _place_adjacent(layouts[pub], width, height, layouts.values(), preferred_sides)
                 if poly:
                     print(f"  🛏️  PRIVATE: {room_name} (attached to {pub})")
                     break

        if poly:
            layouts[room_name] = poly
            if r_type == "bedroom":
                bedroom_names.append(room_name)
            print(f"  🛏️  PRIVATE: {room_name} (branch from core/public)")
        else:
            print(f"  ❌ FAILED to place {room_name} (No valid public adjacency)")
    
    # PHASE 4: SERVICES (Bathrooms, Storage, Utility)
    services = rooms_by_zone["service"]
    study_names = [name for name in layouts.keys() if name.startswith("study")]
    
    bathroom_idx = 0
    
    for idx, room in enumerate(services):
        r_type = room["type"]
        area = float(room["area"])
        aspect_ratio = _random_aspect_ratio(1.0, 0.2)
        height = (area / aspect_ratio) ** 0.5
        width = aspect_ratio * height
        room_index[r_type] = room_index.get(r_type, 0) + 1
        room_name = f"{r_type}_{room_index[r_type]}"
        
        poly = None
        
        # Strategy 1: Ensuite Bathroom (attach to corresponding bedroom, or study if bedrooms are full)
        if r_type == "bathroom":
            target_room = None
            if bathroom_idx < len(bedroom_names):
                target_room = bedroom_names[bathroom_idx]
                bathroom_idx += 1
            elif study_names: # Fallback to study if we have more bathrooms than bedrooms
                target_room = study_names[0] # Try to attach to first study
                study_names.pop(0) # Consume it so multiple baths don't crowd it
                bathroom_idx += 1
                
            if target_room:
                 # Use compactness bias even for ensuites
                 preferred_sides = _get_compact_sides(layouts)
                 poly = _place_adjacent(layouts[target_room], width, height, layouts.values(), preferred_sides)
                 if poly:
                     print(f"  🚿 SERVICE: {room_name} (ensuite to {target_room})")

        # Strategy 2: Common Bath / Storage / Utility
        # Attach to Hall/Living or Kitchen or Corridor
        if not poly:
            preferred_targets = []
            
            # Storage/Utility prefers Kitchen
            if r_type in ["storage", "utility", "pantry"]:
                preferred_targets.extend([n for n in layouts if "kitchen" in n])
            
            # Common Bath prefers Living/Hall
            if r_type == "bathroom":
                preferred_targets.extend([n for n in layouts if "living" in n])
                preferred_targets.extend([n for n in layouts if "study" in n]) # Fallback to any study
                
            # Fallback for all: Living/Hall
            preferred_targets.extend([n for n in layouts if "living" in n])
            
            # Try preferred
            compact_sides = _get_compact_sides(layouts)
            for target in preferred_targets:
                if target in layouts:
                    poly = _place_adjacent(layouts[target], width, height, layouts.values(), compact_sides)
                    if poly:
                        print(f"  🔧 SERVICE: {room_name} (attached to {target})")
                        break
        
        # Strategy 3: Desperation (Attach to anything anywhere)
        if not poly:
             all_rooms = list(layouts.keys())
             random.shuffle(all_rooms)
             compact_sides = _get_compact_sides(layouts)
             for target in all_rooms:
                 poly = _place_adjacent(layouts[target], width, height, layouts.values(), compact_sides)
                 if poly:
                     print(f"  🔧 SERVICE: {room_name} (fallback attached to {target})")
                     break

        if poly:
            layouts[room_name] = poly
        else:
            print(f"  ❌ FAILED to place {room_name}")
    
    # PHASE 5: OTHER (balcony, etc.)
    for room in rooms_by_zone["other"]:
        r_type = room["type"]
        area = float(room["area"])
        aspect_ratio = _random_aspect_ratio(1.0, 0.3)
        height = (area / aspect_ratio) ** 0.5
        width = aspect_ratio * height
        room_index[r_type] = room_index.get(r_type, 0) + 1
        room_name = f"{r_type}_{room_index[r_type]}"
        
        poly = None
        # Balcony prefers living or bedroom
        if r_type == "balcony":
            targets = [name for name in layouts.keys() if name.startswith("living") or name.startswith("bedroom")]
            for target in targets:
                poly = _place_adjacent(layouts[target], width, height, layouts.values())
                if poly:
                    break
        else:
            poly = _place_adjacent(core_poly, width, height, layouts.values())
        
        if poly:
            layouts[room_name] = poly
            print(f"  📦 OTHER: {room_name}")
    
    return layouts

def synthesize_layout_from_spec(spec, config=None):
    rooms = synthesize_single_floor(spec, config)
    
    adjacency_set = set()
    room_names = list(rooms.keys())
    
    print("\n🔍 Detecting geometric adjacencies:")
    for i, r1 in enumerate(room_names):
        for r2 in room_names[i+1:]:
            poly1 = rooms[r1]
            poly2 = rooms[r2]
            intersection = poly1.boundary.intersection(poly2.boundary)
            if not intersection.is_empty and intersection.length > WALL_TOLERANCE:
                pair = tuple(sorted([r1, r2]))
                adjacency_set.add(pair)
                print(f"  ✅ Geometric: {r1} ↔ {r2}")
    
    valid_adjacency = []
    print("\n🛡️  Applying architectural rules:")
    for r1, r2 in adjacency_set:
        t1 = r1.split("_")[0]
        t2 = r2.split("_")[0]
        is_valid, reason = validate_adjacency(t1, t2)
        if is_valid:
            valid_adjacency.append((r1, r2))
            print(f"  ✅ Allowed: {r1} ↔ {r2} ({reason})")
        else:
            print(f"  ❌ Rejected: {r1} ↔ {r2} ({reason})")
    
    corridors = None
    if valid_adjacency:
        try:
            corridors = generate_corridors(rooms, valid_adjacency)
        except Exception as e:
            print(f"⚠️  Corridor: {e}")
    
    doors = None
    if valid_adjacency:
        try:
            opening_specs = []
            for r1, r2 in valid_adjacency:
                width = _determine_opening_width(r1, r2)
                opening_specs.append((r1, r2, width))
            doors = generate_doors(rooms, opening_specs)
            if doors:
                print(f"\n🚪 Generated {len(opening_specs)} openings")
                for (a, b, w) in opening_specs:
                    style = "open space" if w == OPEN_SPACE_WIDTH else "door"
                    print(f"   • {a} ↔ {b} ({style}, {w}m)")
        except Exception as e:
            print(f"⚠️  Doors: {e}")
            import traceback
            traceback.print_exc()
    
    living_rooms = [name for name in rooms.keys() if name.startswith("living")]
    entrance = None
    if living_rooms:
        print("\n🚪 Placing entrance on true external wall:")
        entrance = _generate_entrance_door(rooms[living_rooms[0]], rooms)
        if entrance:
            doors = unary_union([doors, entrance]) if doors else entrance
            print("  ✅ Entrance door placed")
        else:
            print("  ⚠️  Failed to place entrance door")
    
    # Calculate Score
    # Base score 100
    # Penalty for rejected adjacencies
    # Penalty for missing entrance
    
    score = 100
    total_adj = len(adjacency_set)
    valid_adj = len(valid_adjacency)
    
    if total_adj > 0:
        rejected = total_adj - valid_adj
        score -= (rejected * 10) # -10 per bad connection
    
    if not entrance:
        score -= 20
        
    score = max(0, min(100, score))
    
    return {
        "rooms": rooms,
        "corridors": corridors,
        "doors": doors,
        "adjacency": valid_adjacency,
        "entrance": entrance,
        "score": score
    }