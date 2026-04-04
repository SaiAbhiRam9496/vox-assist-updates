import sys
import os
import json
import logging
import asyncio
import random
import csv
from typing import Dict, Optional, Any, List
import uuid

# Add project root to path to allow importing engine
# Add project root and engine to path
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(root_dir)
sys.path.append(os.path.join(root_dir, "backend", "engine"))

from backend.engine.text_to_specs_v2 import ProximityLayoutGenerator
from backend.engine.layout_synthesizer_adjacency import synthesize_layout_from_spec
from backend.engine.resplan_to_3d import build_house_from_layout
from backend.engine.scoring_engine import ScoringEngine
# from backend.engine.floorplan_2d_visualizer import draw_2d_floorplan

logger = logging.getLogger(__name__)

class GenerationService:
    def __init__(self):
        self.architect = ProximityLayoutGenerator()

    async def generate_layout(
        self,
        prompt: str,
        adjacency_pairs: Optional[List[List[str]]] = None,
        rooms_spec: Optional[List[dict]] = None,
    ) -> Dict[str, Any]:
        try:
            # Run blocking ML code in a thread executor
            loop = asyncio.get_event_loop()
            adjacency_pairs = adjacency_pairs or []
            
            # 1. Text to Rooms OR rooms_spec bypass
            SQM_PER_SQFT = 0.092903  # Standard conversion factor

            if rooms_spec:
                # 1a. Detect Total Area from Frontend prompt (e.g., "A house with 1000 sqft...")
                import re
                total_area_sqm = 100.0 # fallback
                match = re.search(r'total area of around (\d+(?:\.\d+)?)\s*sq(ft|m)', prompt.lower())
                if match:
                    val = float(match.group(1))
                    unit = match.group(2)
                    total_area_sqm = val * SQM_PER_SQFT if unit == 'ft' else val
                
                # 1b. Convert manual spec to internal room_list
                room_list = []
                for r in rooms_spec:
                    if r.get("type") and r.get("area"):
                        # Frontend sends sqft in manual mode
                        room_list.append({
                            "type": r["type"].strip().lower(),
                            "area": float(r["area"]) * SQM_PER_SQFT,
                            "auto": False
                        })
                
                # 1c. Standardize (fill missing essentials, distribute remaining area)
                standardized = self.architect.standardize_room_spec(room_list, total_area_sqm, prompt=prompt)
                
                spec = {"rooms": []}
                for r in standardized:
                    spec["rooms"].append({
                        "type": r["type"],
                        "area": r["area"],
                        "name": r.get("name", r["type"]),
                        "instance": r.get("instance", 1),
                        "requested_area_sqft": r.get("requested_area_sqft")
                    })
                logger.info(f"Manual mode standardized: {len(spec['rooms'])} rooms ({total_area_sqm:.1f} total sqm)")
            else:
                # Original path: Parse from text prompt
                # Note: generate_blueprint already calls standardize_room_spec internally
                rooms = await loop.run_in_executor(None, self.architect.generate_blueprint, prompt)
                
                if not rooms:
                    raise ValueError("AI could not generate room list from prompt")
                    
                spec = {"rooms": []}
                for room in rooms:
                    if isinstance(room, dict) and room.get("type") and room.get("area"):
                        # Preserve 'name' (e.g. "bedroom_1") and 'instance' from the
                        # NLP/BHK parser so the synthesizer and display layer can
                        # correctly identify individual room instances (Bedroom 1, Bedroom 2).
                        spec["rooms"].append({
                            "type":                room["type"].strip().lower(),
                            "name":                room.get("name", room["type"].strip().lower()),
                            "instance":            room.get("instance", 1),
                            "area":                float(room["area"]),
                            "requested_area_sqft": int(float(room["area"]) * 10.764)
                        })
            
            if not spec["rooms"]:
                raise ValueError("No valid rooms parsed")

            # 1B. Parse adjacency preferences from text prompt and merge with UI pairs
            nlp_prefer, nlp_avoid = self.architect.parse_adjacency_from_text(prompt)
            if nlp_avoid:
                logger.info(f"NLP detected avoidance hints: {nlp_avoid}")

            merged_pairs: List[List[str]] = []
            seen_pairs = set()
            for pair in (adjacency_pairs + nlp_prefer):
                if not isinstance(pair, list) or len(pair) != 2:
                    continue
                key = tuple(sorted([str(pair[0]).strip().lower(), str(pair[1]).strip().lower()]))
                if key[0] and key[1] and key[0] != key[1] and key not in seen_pairs:
                    merged_pairs.append([key[0], key[1]])
                    seen_pairs.add(key)

            if merged_pairs:
                logger.info(f"Effective soft adjacency pairs: {merged_pairs}")

            # 2. Layout Synthesis & CANDIDATE GENERATION (Best-of-N)
            import uuid
            generation_id = str(uuid.uuid4()) # Unique ID for this generation request
            candidates = []
            
            # Ensure directory exists for models
            models_dir = os.path.join(root_dir, "backend", "static", "models")
            os.makedirs(models_dir, exist_ok=True)

            # Generate 3 candidates
            for i in range(3):
                seed = random.randint(0, 1000000)
                try:
                    # A. Synthesize
                    layout_candidate = await loop.run_in_executor(
                        None, 
                        synthesize_layout_from_spec, 
                        spec, 
                        {"RANDOM_SEED": seed, "adjacency_pairs": merged_pairs}
                    )

                    if not layout_candidate.get("rooms"):
                        logger.warning(f"Candidate {i}: synthesizer returned empty layout, skipping")
                        continue

                    # B. Score
                    adj_satisfaction = layout_candidate.get("adjacency_satisfaction", 1.0)
                    stats_candidate = ScoringEngine.evaluate(layout_candidate, adj_satisfaction)
                    score_candidate = stats_candidate["average"]
                    
                    # C. Generate 3D Model (Unique per candidate)
                    model_id = f"{generation_id}_{i}"
                    model_filename = f"model_{model_id}.ply"
                    output_path = os.path.join(models_dir, model_filename)
                    
                    await loop.run_in_executor(
                        None,
                        build_house_from_layout,
                        layout_candidate,
                        False, # visualize=False
                        output_path
                    )
                    
                    model_url = f"/static/models/{model_filename}"
                    
                    # Add to list
                    # Serialize the layout so it can be sent over JSON
                    serialized_candidate_layout = self._serialize_layout(layout_candidate)
                    
                    # Color Palette (Matching resplan_to_3d.py)
                    ROOM_COLORS = [
                        "#A8DADC", "#F1FAEE", "#A8E6CF", "#FFD3B6", 
                        "#FFAAA5", "#DCEDC1", "#D4A5A5", "#9D8189"
                    ]
                
                    # Generate Spec for this candidate (Colors/Areas)
                    candidate_spec = {"rooms": []}
                    # 1. Get room items exactly as resplan_to_3d does (filtering empty/invalid)
                    from shapely.geometry import Polygon, MultiPolygon
                    generated_rooms_items = []
                    if layout_candidate.get("rooms"):
                        for k, v in layout_candidate["rooms"].items():
                             if v and not v.is_empty and isinstance(v, (Polygon, MultiPolygon)):
                                 generated_rooms_items.append((k, v))
                
                    # 2. Iterate and assign colors by index
                    type_counter: Dict[str, int] = {}
                    for idx, (room_name, poly) in enumerate(generated_rooms_items):
                        r_type = room_name.split('_')[0]
                        type_counter[r_type] = type_counter.get(r_type, 0) + 1
                        type_idx = type_counter[r_type] - 1

                        matching_specs = [r for r in spec["rooms"] if r["type"] == r_type]
                        requested_sqft = matching_specs[type_idx]["requested_area_sqft"] if type_idx < len(matching_specs) else None

                        # Build display name with instance number when there are
                        # multiple rooms of the same type (Bedroom 1, Bedroom 2,
                        # Bathroom 1, Bathroom 2, etc.).
                        total_of_type = len(matching_specs)
                        instance_num  = type_counter[r_type]
                        if total_of_type > 1:
                            display_type = f"{r_type.capitalize()} {instance_num}"
                        else:
                            display_type = r_type.capitalize()

                        area_sqm = poly.area
                        area_sqft = int(area_sqm * 10.764)

                        if requested_sqft and requested_sqft > 0:
                            area_error_pct = round(abs(area_sqft - requested_sqft) / requested_sqft * 100)
                        else:
                            area_error_pct = None
                        
                        candidate_spec["rooms"].append({
                            "id": room_name,
                            "type": display_type,
                            "area": area_sqft,
                            "requested_area_sqft": requested_sqft,
                            "generated_area_sqft": area_sqft,
                            "area_error_pct": area_error_pct,
                            "color": ROOM_COLORS[idx % len(ROOM_COLORS)]
                        })

                    # Gentle area-fidelity penalty (max -10)
                    error_values = [r["area_error_pct"] for r in candidate_spec["rooms"] if r.get("area_error_pct") is not None]
                    if error_values:
                        avg_error = sum(error_values) / len(error_values)
                        fidelity_penalty = min(10.0, avg_error * 0.1)
                        score_candidate = max(0, score_candidate - fidelity_penalty)
                        stats_candidate["area_fidelity_avg_error_pct"] = round(avg_error, 1)
                    else:
                        stats_candidate["area_fidelity_avg_error_pct"] = 0

                    candidates.append({
                        "id": i,
                        "layout": serialized_candidate_layout, 
                        "spec": candidate_spec, # Store spec per candidate
                        "stats": stats_candidate,
                        "score": score_candidate,
                        "model_url": model_url,
                        "seed": seed,
                        "adjacency_satisfaction": adj_satisfaction
                    })

                except ValueError as ve:
                    # Room count mismatch, no placeable rooms, or other spec validation error.
                    # Skip this candidate and try the next seed — do NOT kill the whole job.
                    logger.warning(f"Candidate {i} (seed={seed}) failed validation: {ve}")
                    continue
                except Exception as e:
                    logger.error(f"Candidate {i} (seed={seed}) unexpected error: {e}")
                    continue
            
            if not candidates:
                raise ValueError("Failed to generate any valid candidates")
                
            # 3. Select Best (Default Winner)
            best_candidate = max(candidates, key=lambda x: x['score'])
            
            # Serialize layout for DB (convert Shapely objects to dicts) for the best candidate
            # Note: best_candidate['layout'] is now ALREADY serialized from the loop above!
            serialized_layout = best_candidate['layout']
            display_spec = best_candidate['spec']
            
            # Use computed stats from ScoringEngine
            stats = best_candidate['stats']

            return {
                "success": True,
                "spec": display_spec, # Send sqft + colors to frontend
                "layout": serialized_layout, 
                "model_url": best_candidate["model_url"],
                "design_id": generation_id,
                "score": stats.get("efficiency", 0.0), # EXTRACTED FROM STATS
                "stats": stats, # Detailed scores
                "candidates": candidates, # The Best-of-N candidates for UI
                "adjacency_pairs": merged_pairs,
                "message": "Layout generated successfully"
            }

        except Exception as e:
            logger.error(f"Generation error: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    def _log_to_csv(self, spec, layout):
        import csv
        from shapely.geometry import Polygon
        csv_path = os.path.join(root_dir, "backend", "engine", "layout_data.csv")
        
        rooms = spec["rooms"]
        generated_rooms = layout.get("rooms", {})
        
        # Helper to find specific room
        def find_room(prefix):
            # Returns tuple (room_name, room_spec)
            for r in rooms:
                if r["type"].startswith(prefix):
                    return r
            return None

        # Helper to find geometric bounds (Convert meters -> feet for CSV consistency)
        def get_geom_stats(room_type_prefix):
            # Find key in generated_rooms matching prefix
            for name, poly in generated_rooms.items():
                if name.startswith(room_type_prefix):
                    if isinstance(poly, Polygon):
                        minx, miny, maxx, maxy = poly.bounds
                        return {
                            f"{room_type_prefix}1_x": poly.centroid.x * 3.28084,
                            f"{room_type_prefix}1_y": poly.centroid.y * 3.28084,
                            f"{room_type_prefix}1_w": (maxx - minx) * 3.28084,
                            f"{room_type_prefix}1_h": (maxy - miny) * 3.28084,
                            f"{room_type_prefix}1_aspect": (maxx - minx) / (maxy - miny) if (maxy - miny) > 0 else 1
                        }
            return {}

        # 1. Basic Stats
        total_area = sum(r["area"] for r in rooms)
        n_bedrooms = sum(1 for r in rooms if "bedroom" in r["type"])
        n_bathrooms = sum(1 for r in rooms if "bathroom" in r["type"])
        
        # 2. Specific Areas
        area_map = {}
        counts = {}
        for r in rooms:
            t = r["type"]
            counts[t] = counts.get(t, 0) + 1
            # ... (key mapping omitted for brevity, logic remains same)
            key = f"{t}{counts[t]}_area" if counts[t] > 0 else f"{t}_area"
            if t == "living": key = "living_area"
            elif t == "kitchen": key = "kitchen_area"
            elif t == "balcony": key = "balcony_area"
            elif t == "bedroom": key = f"bedroom{counts[t]}_area"
            elif t == "bathroom": key = f"bathroom{counts[t]}_area"
            area_map[key] = r["area"]

        # 3. Adjacency
        adj_list = layout.get("adjacency", [])
        
        def check_adj(t1, t2):
            # Check if any room of type t1 is connected to any of type t2
            for a, b in adj_list:
                types = [a.split('_')[0], b.split('_')[0]]
                if t1 in types and t2 in types:
                    return 1
            return 0
        
        # Scores Calculation (Mirrors frontend display logic)
        # Scores Calculation (Dynamic ML Engine)
        computed_scores = ScoringEngine.evaluate(layout)
        
        eff_score = computed_scores['efficiency']
        priv_score = computed_scores['privacy']
        day_score = computed_scores['daylight']
        circ_score = computed_scores['circulation']
        avg_score = computed_scores['average']
        
        # 4. Preparing the Row
        row = {
            "image_file": "generated_3d.ply",
            "total_area": total_area,
            "n_bedrooms": n_bedrooms,
            "n_bathrooms": n_bathrooms,
            "has_kitchen": 1 if counts.get("kitchen") else 0,
            "has_dining": 1 if counts.get("dining") else 0,
            "has_balcony": 1 if counts.get("balcony") else 0,
            "has_study": 1 if counts.get("study") else 0,
            "has_store": 1 if counts.get("storage") else 0,
            
            # Areas
            "living_area": area_map.get("living_area", 0),
            "bedroom1_area": area_map.get("bedroom1_area", 0),
            "bedroom2_area": area_map.get("bedroom2_area", 0),
            "kitchen_area": area_map.get("kitchen_area", 0),
            "bathroom1_area": area_map.get("bathroom1_area", 0),
            "bathroom2_area": area_map.get("bathroom2_area", 0),
            "balcony_area": area_map.get("balcony_area", 0),
            
            # Adjacency Features (Graph attributes)
            "adj_living_bedroom": check_adj("living", "bedroom"),
            "adj_living_kitchen": check_adj("living", "kitchen"),
            "adj_living_bathroom": check_adj("living", "bathroom"),
            "adj_bedroom_bathroom": check_adj("bedroom", "bathroom"),
            "adj_kitchen_dining": check_adj("kitchen", "dining"),
            "adj_living_balcony": check_adj("living", "balcony"),
            
            # Geometric Features (Bedroom 1)
            **get_geom_stats("bedroom"),
            
            # Scores & Metadata
            "valid_plan": 1 if eff_score > 0 else 0,
            "efficiency_score": float(eff_score),
            "privacy_score": float(priv_score), # Dummy heuristic
            "daylight_score": float(day_score),
            "circulation_score": float(circ_score), # Placeholder
            
            # Defaults for mandatory fields that might be missing
            "style": 2, # Modern default
            "privacy_priority": 0.5,
            "sunlight_priority": 0.5,
            "circulation_priority": 0.5,
            "door_count": len(layout.get("doors", []) if isinstance(layout.get("doors"), list) else []),
            "avg_path_length": 0,
            "max_path_length": 0,
            "dead_end_count": 0,
            "openness_ratio": 0, 
            "exterior_wall_ratio": 0,
            "bedroom1_rotation": 0
        }
        
        # Write
        file_exists = os.path.isfile(csv_path)
        fieldnames = []
        if file_exists:
            with open(csv_path, 'r', newline='') as f:
                reader = csv.reader(f)
                try:
                    fieldnames = next(reader)
                except StopIteration:
                    pass
        
        if fieldnames:
            try:
                with open(csv_path, 'a', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow(row)
            except IOError as e:
                logger.warning(f"Could not write to CSV at {csv_path}: {e}. File might be locked or inaccessible.")
            except Exception as e:
                logger.error(f"Unexpected error writing to CSV: {e}")
                
        # Return extra stats for frontend
        return {
            "privacy": priv_score,
            "daylight": day_score,
            "circulation": circ_score,
            "efficiency": eff_score,
            "average": avg_score
        }

    def _serialize_layout(self, data: Any) -> Any:
        """Recursively convert Shapely objects to GeoJSON-like dicts"""
        from shapely.geometry import Polygon, Point, MultiPolygon, LineString, MultiLineString
        
        if isinstance(data, dict):
            return {k: self._serialize_layout(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._serialize_layout(i) for i in data]
        
        # Handle Shapely Geometries
        if hasattr(data, "geom_type"):
            if isinstance(data, Polygon):
                return {"type": "Polygon", "coordinates": list(data.exterior.coords)}
            elif isinstance(data, Point):
                return {"type": "Point", "coordinates": list(data.coords)}
            elif isinstance(data, LineString):
                return {"type": "LineString", "coordinates": list(data.coords)}
            elif isinstance(data, MultiPolygon):
                return {"type": "MultiPolygon", "coordinates": [list(g.exterior.coords) for g in data.geoms]}
            elif isinstance(data, MultiLineString):
                return {"type": "MultiLineString", "coordinates": [list(g.coords) for g in data.geoms]}
            else:
                return str(data) # Fallback for unknown geometries
                
        return data

generation_service = GenerationService()