import re
import json
from typing import List, Dict, Tuple, Optional, Set

class ProximityLayoutGenerator:
    def __init__(self):
        self.room_types = [
            'living', 'bedroom', 'kitchen', 'bathroom', 
            'balcony', 'storage', 'garden', 'parking', 'study', 'dining', 'hallway'
        ]
        
        self.synonym_map = {
            'living':   ['living', 'hall', 'lounge', 'family', 'main'],
            'bedroom':  ['bedroom', 'bed', 'master', 'guest', 'bhk'],
            'kitchen':  ['kitchen', 'cooking', 'pantry'],
            'dining':   ['dining', 'breakfast', 'eating'],
            'bathroom': ['bathroom', 'bath', 'washroom', 'toilet', 'restroom'],
            'balcony':  ['balcony', 'veranda', 'deck', 'patio', 'terrace'],
            'storage':  ['storage', 'store', 'closet', 'utility'],
            'garden':   ['garden', 'lawn', 'yard', 'green'],
            'parking':  ['parking', 'garage', 'carport'],
            'study':    ['study', 'office', 'library', 'workspace', 'work'],
            'hallway':  ['hallway', 'corridor', 'passage', 'circulation']
        }
        
        self.word_to_num = {
            'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'single': 1, 'double': 2, 'triple': 3, 'a': 1, 'an': 1
        }
        
        self.default_ratios = {
            'living': 0.35, 'bedroom': 0.25, 'kitchen': 0.15,
            'bathroom': 0.10, 'balcony': 0.10, 'study': 0.10, 'dining': 0.15
        }

        # Minimum realistic areas (sqm) - Converted from sqft roughly / 10
        # Increased to ensure no 50 sqft bedrooms (approx 5 sqm)
        self.min_areas = {
            'bedroom': 10, 'kitchen': 8, 'bathroom': 5,
            'living': 16, 'storage': 4, 'balcony': 5, 'garden': 5,
            'study': 6, 'dining': 9
        }

        # Will hold metadata from the last parsed prompt
        self.last_metadata = None

    def _extract_total_area(self, text: str) -> int:
        """Extract total area without modifying text."""
        # Extended patterns to catch "900 sqft", "900 sq. ft.", "900m2", etc.
        patterns = [
            r'(\d+)\s*(?:square\s+feet|sqft|sq\s+ft|sq\.ft|ft2)',
            r'(\d+)\s*(?:square\s+meters|sqm|sq\s+m|m2)',
            r'total.*?(\d+)\s*(?:square|sq)',
            r'(\d+)\s*(?:square|sq).*?total'
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        # Fallback: look for just a large number if explicitly requested "house of 900"
        match = re.search(r'house\s+of\s+(\d+)', text, re.IGNORECASE)
        if match:
            return int(match.group(1))
            
        return 150  # Default 150 sqm (~1600 sqft)

    def _detect_units(self, text: str) -> Optional[str]:
        """Detect if text implies sqft or sqm. Default to sqft if ambiguous high numbers."""
        if re.search(r'(?:sqm|m2|meters)', text, re.IGNORECASE):
            return 'sqm'
        if re.search(r'(?:sqft|ft2|feet)', text, re.IGNORECASE):
            return 'sqft'
        return None

    def _find_room_mentions(self, text: str) -> List[Dict[str, any]]:
        """Find all room mentions with canonical type and position, merging duplicates."""
        mentions = []
        for canonical, synonyms in self.synonym_map.items():
            for word in synonyms:
                # Match word with optional 's' and word boundaries
                for match in re.finditer(rf'\b({word})s?\b', text, re.IGNORECASE):
                    mentions.append({
                        'start': match.start(),
                        'end': match.end(),
                        'word': match.group().lower(),
                        'type': canonical
                    })
        # Sort by position
        mentions.sort(key=lambda x: x['start'])
        
        # Merge adjacent mentions of same type (e.g. "Master Bedroom")
        # If current starts within X chars of previous end
        unique_mentions = []
        if mentions:
            curr = mentions[0]
            for next_m in mentions[1:]:
                # If overlap or close adjacency (e.g. "Master Bedroom" -> space is 1 char)
                if next_m['type'] == curr['type'] and next_m['start'] <= curr['end'] + 5:
                    # Extend current
                    curr['end'] = max(curr['end'], next_m['end'])
                    curr['word'] += " " + next_m['word'] 
                else:
                    unique_mentions.append(curr)
                    curr = next_m
            unique_mentions.append(curr)
            
        # --- REFERENCE FILTERING ---
        # Remove mentions that are just references (e.g. "connected to the kitchen")
        # preventing duplicates and area stealing.
        final_mentions = []
        connection_pattern = re.compile(r'(connected|attached|access|leads?|next|has)\s+to\s+(the\s+|a\s+|an\s+)?', re.IGNORECASE)
        
        for m in unique_mentions:
            start = m['start']
            # Check if this mention is preceded by a connection word
            context_before = text[max(0, start - 50):start]
            if connection_pattern.search(context_before):
                # This is a reference, skip it
                continue
            final_mentions.append(m)
        
        return final_mentions

    def _get_local_context(self, text, pos, window=30):
        """Get substring around a position."""
        start = max(0, pos - window)
        end = min(len(text), pos + window)
        return text[start:end], start

    def _parse_count(self, context, room_word):
        """Parse count from local context."""
        # Check digit: "2 bedrooms"
        digit_match = re.search(rf'(\d+)\s*{re.escape(room_word)}', context, re.IGNORECASE)
        if digit_match:
            return int(digit_match.group(1))
        
        # Check word: "two bedrooms"
        for word, num in self.word_to_num.items():
            if re.search(rf'\b{word}\s+{re.escape(room_word)}', context, re.IGNORECASE):
                return num
        return 1

    def _parse_areas(self, context, context_offset, total_area, room_pos):
        """Extract areas near room_pos, excluding total_area."""
        areas = []
        for match in re.finditer(r'\d+', context):
            val = int(match.group())
            if val < 5 or val == total_area: # Lower threshold for sqm
                continue
            # Absolute position of number
            abs_pos = context_offset + match.start()
            dist = abs(abs_pos - room_pos)
            if dist < 25:  # Only consider close numbers
                areas.append((dist, val))
        areas.sort()  # by distance
        return [val for _, val in areas]

    def _log(self, msg):
        pass

    def parse_natural_language(self, text):
        original_text = text.lower()
        raw_total_area = self._extract_total_area(original_text) 
        
        # Detect units and normalize to sqm
        units = self._detect_units(original_text)
        
        if units == 'sqft' or (units is None and raw_total_area > 400):
            scale_factor = 0.092903 
            total_area = int(raw_total_area * scale_factor)
        else:
            scale_factor = 1.0
            total_area = raw_total_area
            
        mentions = self._find_room_mentions(original_text)
        if not mentions:
            return total_area, [{'type': 'living', 'area': total_area, 'auto': False}], total_area, set()

        # --- GLOBAL NUMBER ASSIGNMENT ---
        # Find all numbers and assign to closest VALID room mention
        # This prevents "stealing" (e.g. Master Bedroom taking Utility's 100sqft)
        # And prevents references from taking backward numbers (e.g. "600 ... connected to dining")
        
        number_assignments = {i: [] for i in range(len(mentions))}
        connection_pattern = re.compile(r'(connected|attached|access|leads?|next)\s+to\s+(the\s+|a\s+|an\s+)?$', re.IGNORECASE)
        
        for match in re.finditer(r'\d+', original_text):
            val = int(match.group())
            # Skip likely Total Area or tiny numbers
            if val == raw_total_area or val < 5: 
                continue
                
            pos = match.start()
            
            # Find closest mention
            candidates = []
            
            for i, m in enumerate(mentions):
                # Calculate distance to mention (closest edge)
                if pos < m['start']:
                    dist = m['start'] - pos
                elif pos > m['end']:
                    dist = pos - m['end']
                else:
                    dist = 0 # Inside
                
                # Check if this mention is a "Backward Reference"
                # If number is BEFORE mention, and mention is "connected to X", it shouldn't take it.
                is_before = pos < m['start']
                pre_text = original_text[max(0, m['start']-30):m['start']]
                is_ref = bool(connection_pattern.search(pre_text))
                
                # Penalize assigning a number to a room that was mentioned previously (backward parsing)
                dist_penalty = 0 if is_before else 20
                
                if dist < 60:
                    candidates.append((dist + dist_penalty, i, is_ref, is_before))
            
            # Sort by distance
            candidates.sort(key=lambda x: x[0])
            
            assigned = False
            for dist, idx, is_ref, is_before in candidates:
                # If it's a reference and number is before it, skip (heuristic)
                # Unless it's REALLY close? No, "600 ... connected to dining". 
                if is_ref and is_before:
                    continue
                
                number_assignments[idx].append(val)
                assigned = True
                break
                
            if not assigned and candidates:
                # Fallback: assign to closest regardless (e.g. "Bedroom 1")
                # But only if NOT a reference?
                # If all are references, we might lose the number.
                # Let's just assign to valid closest if exists.
                 pass

        explicit_rooms = []
        excluded_types = set()
        used_area = 0

        for idx, mention in enumerate(mentions):
            room_type = mention['type']
            room_pos = mention['start']
            room_word = mention['word']
            
            # Context for Count and Negation
            context_start = max(0, room_pos - 40)
            context_end = min(len(original_text), room_pos + 40)
            context = original_text[context_start:context_end]

            # --- CONDITIONAL REFERENCE FILTER ---
            # If "connected to <room>" AND no area assigned -> Skip (It's a reference)
            # If "connected to <room>" AND has area -> Keep (It's an inline definition)
            pre_context = original_text[max(0, room_pos - 30):room_pos].lower()
            areas = number_assignments[idx]
            
            if connection_pattern.search(pre_context) and not areas:
                continue
            # ------------------------------------

            # --- NEGATION CHECK ---
            pre_short = original_text[max(0, room_pos - 15):room_pos].lower()
            if re.search(r'\b(no|without)\b', pre_short):
                excluded_types.add(room_type)
                continue
            
            # Parse count
            count = self._parse_count(context, room_word)
            
            # --- DEDUPLICATION & MERGING ---
            existing_indices = [i for i, r in enumerate(explicit_rooms) if r['type'] == room_type]
            
            if areas:
                assigned_areas_idx = 0
                
                # 1. Update existing 'auto' rooms
                for e_idx in existing_indices:
                    if explicit_rooms[e_idx]['auto'] and assigned_areas_idx < len(areas):
                        area = int(areas[assigned_areas_idx] * scale_factor)
                        explicit_rooms[e_idx]['area'] = area
                        explicit_rooms[e_idx]['auto'] = False
                        used_area += area
                        assigned_areas_idx += 1
                
                # 2. Add remaining
                while assigned_areas_idx < len(areas):
                     area = int(areas[assigned_areas_idx] * scale_factor)
                     explicit_rooms.append({'type': room_type, 'area': area, 'auto': False})
                     used_area += area
                     assigned_areas_idx += 1

            else:
                needed = count - len(existing_indices)
                for _ in range(needed):
                    explicit_rooms.append({'type': room_type, 'area': 0, 'auto': True})

        return total_area, explicit_rooms, used_area, excluded_types

    def generate_blueprint(self, prompt: str) -> List[Dict[str, any]]:
        print(f"\n--- PROMPT: \"{prompt}\" ---")
        total_area, room_list, used_area, excluded_types = self.parse_natural_language(prompt)
        # Expose basic metadata for external consumers (e.g., CSV export)
        self.last_metadata = {
            "total_area": total_area,
            "used_area_initial": used_area,
            "rooms_initial": [r.copy() for r in room_list],
        }
        
        # Validate and clamp areas
        for room in room_list:
            if not room['auto'] and room['area'] > 0:
                min_area = self.min_areas.get(room['type'], 20)
                if room['area'] < min_area:
                    print(f"[!] Warning: {room['type']} area {room['area']} too small. Clamping to {min_area}.")
                    room['area'] = min_area

        # --- FILL MISSING ESSENTIALS ---
        # We only force essentials: Living, Bedroom, Kitchen, Bathroom, Hallway
        # Optional rooms (Storage, Balcony, etc.) are NOT added automatically unless mentioned
        essential_types = ['living', 'bedroom', 'kitchen', 'bathroom', 'hallway']
        existing_types = {r['type'] for r in room_list}
        
        missing_essentials = [t for t in essential_types if t not in existing_types]
        
        # We process this BEFORE calculating remaining area so they get a share
        if missing_essentials:
            print(f"Auto-adding missing essentials: {missing_essentials}")
            for t in missing_essentials:
                # DO NOT ADD IF EXPLICITLY NEGATED
                if t in excluded_types:
                    print(f"Skipping essential {t} because it was negated.")
                    continue
                    
                room_list.append({
                    'type': t,
                    'area': 0, # Will be filled by auto logic
                    'auto': True
                })
        # -------------------------------

        # Smart Filling Strategy
        # 1. Update remaining area based on explicit rooms
        used_area = sum(r['area'] for r in room_list if not r['auto'])
        remaining_area = max(0, total_area - used_area)
        
        print(f"Total: {total_area} | Explicit Used: {used_area} | Remaining: {remaining_area}")

        # 2. Get list of auto rooms
        auto_rooms = [r for r in room_list if r['auto']]
        
        if auto_rooms:
            if remaining_area <= 0:
                # No space left? Give them min areas, then we'll scale everything down
                for r in auto_rooms:
                    r['area'] = self.min_areas.get(r['type'], 5)
            else:
                # Rule 3: Default distribution
                DEFAULTS = {
                    "living": 0.28,
                    "bedroom": 0.31, # combined master (18) + bedroom (13)
                    "kitchen": 0.12,
                    "dining": 0.10,
                    "bathroom": 0.08,
                    "hallway": 0.07,
                    "storage": 0.04
                }
                
                # Rule 2: "bigger [room]" -> 1.5x default percentage
                amplified_rooms = set()
                for t in self.room_types:
                    for syn in self.synonym_map.get(t, [t]):
                        if re.search(r'\b(bigger|larger|huge|massive|large)\s+' + syn + r'\b', prompt.lower()):
                            amplified_rooms.add(t)
                
                adjusted_defaults = {}
                for t, pct in DEFAULTS.items():
                    adjusted_defaults[t] = pct * 1.5 if t in amplified_rooms else pct
                
                # Count instances of each auto room type for equal splitting
                type_counts = {}
                for r in auto_rooms:
                    type_counts[r['type']] = type_counts.get(r['type'], 0) + 1
                
                # Normalize percentages of unspecified rooms to sum to 1
                total_pct = 0
                for r in auto_rooms:
                    base_ratio = adjusted_defaults.get(r['type'], 0.1)
                    r_ratio = base_ratio / type_counts[r['type']]
                    total_pct += r_ratio
                
                if total_pct == 0: total_pct = 1
                
                # Distribute remaining
                for r in auto_rooms:
                    base_ratio = adjusted_defaults.get(r['type'], 0.1)
                    r_ratio = base_ratio / type_counts[r['type']]
                    share = (r_ratio / total_pct) * remaining_area
                    
                    # Rule 5: No single room > 35% of total area unless explicitly requested
                    max_allowed = total_area * 0.35
                    new_area = int(share)
                    if new_area > max_allowed:
                        new_area = int(max_allowed)
                    
                    r['area'] = max(self.min_areas.get(r['type'], 5), new_area)
                    
        # 3. Final Scaling to match Total Area exactly
        current_total = sum(r['area'] for r in room_list)
        if current_total > 0 and abs(current_total - total_area) > 1:
            scale = total_area / current_total
            print(f"Scaling design by {scale:.2f} to fit {total_area}")
            for r in room_list:
                r['area'] = int(r['area'] * scale)

        return room_list


if __name__ == "__main__":
    architect = ProximityLayoutGenerator()
    
    prompt = "I need an ideal house in 1500 sqft."
    
    result = architect.generate_blueprint(prompt)
    print(json.dumps(result, indent=4))