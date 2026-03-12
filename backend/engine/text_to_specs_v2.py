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
            # Rule 12: 'hall'/'main' removed from living (false matches on hallway/main entrance)
            # Rule 18: 'master'/'guest' are instance_qualifiers only — not standalone synonyms
            'living':   ['living', 'living area', 'lounge', 'family'],
            'bedroom':  ['bedroom', 'bed'],
            'kitchen':  ['kitchen', 'cooking', 'pantry'],
            'dining':   ['dining', 'dining area', 'breakfast', 'eating'],
            'bathroom': ['bathroom', 'bath', 'washroom', 'toilet', 'restroom'],
            'balcony':  ['balcony', 'veranda', 'deck', 'patio', 'terrace'],
            'storage':  ['storage', 'store', 'closet', 'utility'],
            'garden':   ['garden', 'lawn', 'yard', 'green'],
            'parking':  ['parking', 'garage', 'carport'],
            'study':    ['study', 'study room', 'office', 'home office', 'library', 'workspace'],
            'hallway':  ['hallway', 'corridor', 'passage', 'circulation']
        }

        # Rule 18: qualifier words that signal a distinct room instance
        self.instance_qualifiers = [
            'first', 'second', 'third', 'fourth', 'fifth',
            'master', 'guest', 'primary', 'secondary',
            'main', 'common', 'attached', 'ensuite', 'kids'
        ]
        
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
            'bedroom': 10, 'kitchen': 8, 'bathroom': 4.5,
            'living': 16, 'storage': 4, 'balcony': 5, 'garden': 5,
            'study': 6, 'dining': 9, 'hallway': 4
        }

        # Will hold metadata from the last parsed prompt
        self.last_metadata = None

    def _extract_total_area(self, text: str) -> int:
        """Extract the total / overall house area from the prompt."""
        UNIT_SUFFIX = r'(?:square\s+feet|sqft|sq\s+ft|sq\.?\s*ft|ft2|square\s+meters?|sqm|sq\s+m|m2|sq)'

        # Priority 1: explicit total markers (e.g., "Total = 1500 sqft")
        explicit_patterns = [
            rf'total\s*(?:area)?\s*[=:]\s*(\d[\d,]*)\s*{UNIT_SUFFIX}',
            rf'total\s+area\s+(?:of\s+)?(\d[\d,]*)\s*{UNIT_SUFFIX}',
            rf'overall\s+(?:area\s+)?(?:of\s+)?(\d[\d,]*)\s*{UNIT_SUFFIX}',
            rf'(\d[\d,]*)\s*{UNIT_SUFFIX}\s+(?:total|in\s+total|overall)',
            rf'grand\s+total\s+(?:of\s+)?(\d[\d,]*)\s*{UNIT_SUFFIX}',
        ]

        for pattern in explicit_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(1).replace(',', '')
                return int(raw)

        # Priority 2: legacy fallback (first bare unit occurrence)
        legacy_patterns = [
            r'(\d[\d,]*)\s*(?:square\s+feet|sqft|sq\s+ft|sq\.?\s*ft|ft2)',
            r'(\d[\d,]*)\s*(?:square\s+meters?|sqm|sq\s+m|m2)',
            r'total.*?(\d+)\s*(?:square|sq)',
            r'(\d+)\s*(?:square|sq).*?total',
        ]
        for pattern in legacy_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1).replace(',', ''))

        # Priority 3: look for just a large number if explicitly requested "house of 900"
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

    def _find_room_mentions(self, text: str) -> List[Dict]:
        """
        Find all room mentions with canonical type and position.
        Rule 5:  word-boundary matching, reference filtering.
        Rule 18: qualifier words (master/guest/first/second) before a room
                 signal a distinct instance — prevent merging.
        Rule 22: extend span when "room" follows the synonym word.
        """
        mentions = []
        for canonical, synonyms in self.synonym_map.items():
            for word in synonyms:
                for match in re.finditer(rf'\b({re.escape(word)})s?\b', text, re.IGNORECASE):
                    # Rule 22: extend end to include "room" if it follows
                    end_pos = match.end()
                    tail = text[end_pos: end_pos + 6]
                    if re.match(r'\s*room\b', tail, re.IGNORECASE):
                        end_pos += re.match(r'\s*room\b', tail, re.IGNORECASE).end()

                    # Rule 18: check for qualifier immediately before this mention
                    pre = text[max(0, match.start() - 20): match.start()].lower().strip()
                    qualifier = None
                    for q in self.instance_qualifiers:
                        if re.search(rf'\b{re.escape(q)}\s+$', pre):
                            qualifier = q
                            break

                    mentions.append({
                        'start': match.start(),
                        'end':   end_pos,
                        'word':  text[match.start(): end_pos].lower(),
                        'type':  canonical,
                        'qualifier': qualifier,
                    })

        mentions.sort(key=lambda x: x['start'])

        # Dedup: same start + same type → keep longest match only
        deduped: List[Dict] = []
        i = 0
        while i < len(mentions):
            best = mentions[i]
            j = i + 1
            while j < len(mentions) and mentions[j]['start'] == best['start']:
                if mentions[j]['type'] == best['type'] and mentions[j]['end'] > best['end']:
                    best = mentions[j]
                j += 1
            deduped.append(best)
            i = j
        mentions = deduped

        # Rule 18-aware merge: only merge if within 15 chars AND no qualifier on either
        unique_mentions = []
        if mentions:
            curr = mentions[0]
            for nxt in mentions[1:]:
                close        = (nxt['start'] - curr['end']) <= 2  # only truly adjacent e.g. 'master bedroom'
                same_type    = nxt['type'] == curr['type']
                no_qualifier = curr['qualifier'] is None and nxt['qualifier'] is None
                if close and same_type and no_qualifier:
                    curr['end']   = max(curr['end'], nxt['end'])
                    curr['word'] += ' ' + nxt['word']
                else:
                    unique_mentions.append(curr)
                    curr = nxt
            unique_mentions.append(curr)

        # Reference filter — only filter if this room type was already seen
        # e.g. "living room connected to the dining room" → dining FIRST occurrence, keep it
        # but "...and a second kitchen" after kitchen was mentioned → filter the reference
        conn_re = re.compile(
            r'(connected|attached|leads?|next)\s+to\s+(the\s+|a\s+|an\s+)?',
            re.IGNORECASE
        )
        final_mentions = []
        seen_types: Set[str] = set()
        for m in unique_mentions:
            ctx_before = text[max(0, m['start'] - 50): m['start']]
            is_ref = bool(conn_re.search(ctx_before))
            # Only skip if it's a reference AND we already have this room type
            if is_ref and m['type'] in seen_types:
                continue
            seen_types.add(m['type'])
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
            if dist < 40:  # Only consider close numbers (increased from 25 to handle "Room – 200 sqft" format)
                areas.append((dist, val))
        areas.sort()  # by distance
        return [val for _, val in areas]

    def _log(self, msg):
        pass

    def parse_adjacency_from_text(self, text: str):
        prefer_pairs: List[List[str]] = []
        avoid_pairs: List[List[str]] = []

        def resolve(phrase: str) -> Optional[str]:
            phrase = phrase.lower().strip()
            for canonical, synonyms in self.synonym_map.items():
                for syn in synonyms:
                    if syn in phrase or phrase in syn:
                        return canonical
            return None

        def add_prefer(a, b):
            t1, t2 = resolve(a), resolve(b)
            if t1 and t2 and t1 != t2:
                pair = sorted([t1, t2])
                if pair not in prefer_pairs:
                    prefer_pairs.append(pair)

        def add_avoid(a, b):
            t1, t2 = resolve(a), resolve(b)
            if t1 and t2 and t1 != t2:
                pair = sorted([t1, t2])
                if pair not in avoid_pairs:
                    avoid_pairs.append(pair)

        RM = r'((?:master\s+|guest\s+|en[\s-]?suite\s+)?[a-z]+(?:\s+room|\s+area)?)'

        adj_patterns = [
            rf'{RM}\s+(?:should\s+be\s+|must\s+be\s+|needs?\s+to\s+be\s+)?adjacent\s+to\s+{RM}',
            rf'{RM}\s+(?:next\s+to|near|close\s+to)\s+{RM}',
            rf'{RM}\s+(?:connected\s+to|attached\s+to)\s+{RM}',
            rf'{RM}\s+and\s+{RM}\s+(?:should\s+be\s+|must\s+be\s+)?adjacent',
            rf'(?:place|put|keep)\s+{RM}\s+(?:beside|next\s+to|adjacent\s+to)\s+{RM}',
        ]

        avoid_patterns = [
            rf'{RM}\s+should\s+not\s+(?:be\s+)?(?:adjacent\s+to\s+|next\s+to\s+)?{RM}',
            rf'keep\s+{RM}\s+(?:away\s+from|separate\s+from)\s+{RM}',
            rf'{RM}\s+(?:far\s+from|separate\s+from)\s+{RM}',
            rf'do\s+not\s+(?:place|put|keep)\s+{RM}\s+(?:beside|next\s+to|adjacent\s+to)\s+{RM}',
        ]

        ltext = text.lower()

        for pattern in adj_patterns:
            for m in re.finditer(pattern, ltext, re.IGNORECASE):
                add_prefer(m.group(1).strip(), m.group(2).strip())

        for pattern in avoid_patterns:
            for m in re.finditer(pattern, ltext, re.IGNORECASE):
                add_avoid(m.group(1).strip(), m.group(2).strip())

        return prefer_pairs, avoid_pairs

    def _extract_combined_rooms(self, text: str, total_area: float, scale_factor: float) -> List[Dict]:
        """Rule 24: split 'kitchen and dining of 300 sqft combined' equally between two rooms."""
        UNIT = r'(?:square\s+feet|sqft|sq\s+ft|sq\.?\s*ft|ft2|square\s+meters?|sqm|sq\s+m|m2)'
        pattern = re.compile(
            rf'(\w+(?:\s+room)?)\s+and\s+(\w+(?:\s+room)?)\s+(?:of|for)?\s*(\d[\d,]*(?:\.\d+)?)\s*{UNIT}\s+(?:combined|together|total)',
            re.IGNORECASE
        )
        result = []
        for m in pattern.finditer(text):
            t1 = self._resolve_room_type(m.group(1).strip())
            t2 = self._resolve_room_type(m.group(2).strip())
            area_raw = float(m.group(3).replace(',', ''))
            is_sqft = bool(re.search(r'sqft|sq\s+ft|feet|ft2', m.group(0), re.IGNORECASE))
            area_sqm = area_raw * 0.092903 if is_sqft else area_raw * scale_factor
            split = round(area_sqm / 2, 2)
            if t1 and t2 and t1 != t2 and split < total_area:
                result.append({'type': t1, 'area': split, 'auto': False, 'explicit_area': True})
                result.append({'type': t2, 'area': split, 'auto': False, 'explicit_area': True})
                print(f"[Combined] {t1}+{t2} each={split:.1f}sqm")
        return result

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
            return total_area, [{'type': 'living', 'area': total_area, 'auto': False, 'instance': 1, 'name': 'living'}], total_area, set()

        # Rule 24: extract combined rooms before main loop
        combined_rooms = self._extract_combined_rooms(original_text, total_area, scale_factor)
        combined_types: Set[str] = {r['type'] for r in combined_rooms}

        # --- GLOBAL NUMBER ASSIGNMENT (Rules 6,13,15,19,20,22,23) ---
        UNIT_RE = re.compile(
            r'(?:square\s+feet|sqft|sq\s+ft|sq\.?\s*ft|ft2|square\s+meters?|sqm|sq\s+m|m2)',
            re.IGNORECASE
        )
        MULTI_APPROX = [
            r'somewhere\s+around', r'at\s+least', r'at\s+most',
            r'no\s+less\s+than', r'no\s+more\s+than', r'not\s+less\s+than',
            r'not\s+more\s+than', r'around\s+about', r'in\s+the\s+range\s+of',
        ]

        number_assignments = {i: [] for i in range(len(mentions))}
        connection_pattern = re.compile(r'(connected|attached|leads?|next)\s+to\s+(the\s+|a\s+|an\s+)?$', re.IGNORECASE)

        # Build number entries — only unit-bearing numbers qualify as room areas
        number_entries = []
        for match in re.finditer(r'(\d[\d,]*(?:\.\d+)?)', original_text):
            raw_val = float(match.group(1).replace(',', ''))
            pos     = match.start()
            after   = original_text[pos: pos + len(match.group(1)) + 15]
            has_unit = bool(UNIT_RE.search(after))
            # Rule 15: ≤9 without unit = count; Rule 19: >9 without unit = skip
            if not has_unit:
                continue
            # Skip total area value
            if int(raw_val) == raw_total_area:
                continue
            # Convert to raw value (scale applied per room below)
            unit_match = UNIT_RE.search(after)
            unit_str   = unit_match.group(0).lower() if unit_match else ''
            is_sqft    = any(x in unit_str for x in ['sqft', 'feet', 'ft', 'ft2'])
            val_sqm    = raw_val * 0.092903 if is_sqft else raw_val
            number_entries.append({'pos': pos, 'raw': raw_val, 'val_sqm': val_sqm, 'claimed': False})

        for entry in number_entries:
            pos = entry['pos']
            candidates = []
            for i, m in enumerate(mentions):
                if pos < m['start']:
                    dist = m['start'] - pos
                elif pos > m['end']:
                    dist = pos - m['end']
                else:
                    dist = 0
                is_before = pos < m['start']
                pre_text  = original_text[max(0, m['start'] - 30): m['start']]
                is_ref    = bool(connection_pattern.search(pre_text))

                # Rule 6+23: numbers before rooms are usually counts, not areas
                # Apply penalty unless very close (≤10 chars) — "550sqft living room"
                if is_before:
                    penalty = 0 if dist <= 10 else 15
                else:
                    between = original_text[m['end']: pos].strip()
                    # empty between = direct attribution (e.g. "bedroom 300 sqft")
                    soft = (between == '') or bool(re.match(
                        r'^(?:of|is|are|was|were|=|around|about|approximately|roughly|nearly)\b',
                        between, re.IGNORECASE
                    ))
                    if not soft:
                        for approx in MULTI_APPROX:
                            if re.search(rf'^{approx}', between, re.IGNORECASE):
                                soft = True
                                break
                    # Also catch "around/about/approx" anywhere in a short between-string
                    # e.g. "should be the central space, around 550"
                    if not soft and len(between) <= 60:
                        if re.search(r'\b(around|about|approximately|roughly|nearly)\b', between, re.IGNORECASE):
                            soft = True
                    # soft connector = direct attribution — use negative bonus so this room wins
                    penalty = -10 if soft else 20

                # Rule 25: skip storage mentions preceded by "with" (embedded qualifiers)
                pre_25 = original_text[max(0, m['start'] - 25): m['start']]
                if m['type'] == 'storage' and re.search(r'\bwith\b', pre_25, re.IGNORECASE):
                    continue

                if dist < 60:
                    candidates.append((dist + penalty, i, is_ref, is_before))

            candidates.sort(key=lambda x: x[0])
            for _, idx, is_ref, is_before in candidates:
                if is_ref and is_before:
                    continue
                # Rule 13: first claim wins
                if not entry['claimed']:
                    number_assignments[idx].append(entry['val_sqm'])
                    entry['claimed'] = True
                break

        explicit_rooms = []
        excluded_types = set()
        used_area = 0
        # Rule 24: seed with combined rooms
        explicit_rooms.extend(combined_rooms)
        used_area += sum(r['area'] for r in combined_rooms)

        for idx, mention in enumerate(mentions):
            room_type = mention['type']
            room_pos = mention['start']
            room_word = mention['word']

            # Rule 24: skip if already handled as combined
            if room_type in combined_types:
                continue
            
            # Context for Count and Negation
            context_start = max(0, room_pos - 50)  # Increased from 40
            context_end = min(len(original_text), room_pos + 50)  # Increased from 40
            context = original_text[context_start:context_end]

            # --- CONDITIONAL REFERENCE FILTER ---
            # Only skip if it's a reference AND this room type already exists
            # e.g. "living room connected to the dining" → first dining occurrence → keep
            pre_context = original_text[max(0, room_pos - 30):room_pos].lower()
            areas = number_assignments[idx]
            already_have_type = any(r['type'] == room_type for r in explicit_rooms)
            
            if connection_pattern.search(pre_context) and not areas and already_have_type:
                continue
            # ------------------------------------

            # --- NEGATION CHECK ---
            pre_short = original_text[max(0, room_pos - 15):room_pos].lower()
            if re.search(r'\b(no|without)\b', pre_short):
                excluded_types.add(room_type)
                continue

            # Rule 25: skip embedded storage/closet mentions preceded by "with" with no area
            pre_25 = original_text[max(0, room_pos - 25): room_pos]
            areas = number_assignments[idx]
            if room_type == 'storage' and re.search(r'\bwith\b', pre_25, re.IGNORECASE) and not areas:
                continue
            
            # Parse count — use canonical type word (e.g. 'bedroom') not compound match
            count = self._parse_count(context, room_type)
            
            # --- DEDUPLICATION & MERGING ---
            existing_indices = [i for i, r in enumerate(explicit_rooms) if r['type'] == room_type]
            
            if areas:
                # Rule 16: "each"/"every" → replicate first area to all count instances
                each_match = re.search(
                    r'\beach\b|\bevery\b',
                    original_text[room_pos: min(len(original_text), room_pos + 60)],
                    re.IGNORECASE
                )
                if each_match and count > 1:
                    shared_area = round(areas[0], 2)
                    # Rule 14: discard if >= total
                    if shared_area >= total_area:
                        shared_area = 0
                    rooms_to_create = count - len(existing_indices)
                    for _ in range(rooms_to_create):
                        if shared_area > 0:
                            explicit_rooms.append({'type': room_type, 'area': shared_area, 'auto': False, 'explicit_area': True})
                            used_area += shared_area
                        else:
                            explicit_rooms.append({'type': room_type, 'area': 0, 'auto': True})
                else:
                    assigned_areas_idx = 0
                    rooms_created = 0

                    # 1. Update existing auto rooms
                    for e_idx in existing_indices:
                        if explicit_rooms[e_idx]['auto'] and assigned_areas_idx < len(areas):
                            area = round(areas[assigned_areas_idx], 2)
                            if area < total_area:  # Rule 14
                                explicit_rooms[e_idx]['area'] = area
                                explicit_rooms[e_idx]['auto'] = False
                                explicit_rooms[e_idx]['explicit_area'] = True
                                used_area += area
                                rooms_created += 1
                            assigned_areas_idx += 1

                    # 2. Add new explicit rooms from remaining areas
                    while assigned_areas_idx < len(areas):
                        area = round(areas[assigned_areas_idx], 2)
                        if area < total_area:  # Rule 14
                            explicit_rooms.append({'type': room_type, 'area': area, 'auto': False, 'explicit_area': True})
                            used_area += area
                            rooms_created += 1
                        assigned_areas_idx += 1

                    # Rule 7: count > rooms_created → fill shortfall as auto
                    total_now = len(existing_indices) + rooms_created
                    for _ in range(max(0, count - total_now)):
                        explicit_rooms.append({'type': room_type, 'area': 0, 'auto': True})

            else:
                needed = count - len(existing_indices)
                for _ in range(max(0, needed)):
                    explicit_rooms.append({'type': room_type, 'area': 0, 'auto': True})

        # Rule 11: assign instance + name fields before returning
        type_totals: Dict[str, int] = {}
        for r in explicit_rooms:
            type_totals[r['type']] = type_totals.get(r['type'], 0) + 1
        type_cur: Dict[str, int] = {}
        named_rooms = []
        for r in explicit_rooms:
            t = r['type']
            type_cur[t] = type_cur.get(t, 0) + 1
            instance = type_cur[t]
            name = f"{t}_{instance}" if type_totals[t] > 1 else t
            named_rooms.append({**r, 'instance': instance, 'name': name})

        print(f"[NLP] {len(named_rooms)} rooms, explicit: {round(used_area, 1)} sqm")
        return total_area, named_rooms, used_area, excluded_types

    # ────────────────────────────────────────────────────────────────────────────────
    # FAST-PATH PARSER (for structured text prompts)
    # ────────────────────────────────────────────────────────────────────────────────
    def _has_explicit_room_areas(self, text: str) -> bool:
        """Rule 4 guard: True if text has more than 1 number-with-unit (beyond just the total)."""
        UNIT = r'(?:square\s+feet|sqft|sq\s+ft|sq\.?\s*ft|ft2|square\s+meters?|sqm|sq\s+m|m2)'
        return len(re.findall(rf'\d[\d,]*\s*{UNIT}', text, re.IGNORECASE)) > 1

    def _parse_bhk_shorthand(self, text: str, total_sqm: float) -> Optional[List[Dict]]:
        """Rule 4: BHK expansion — fires only when shorthand present AND no explicit room areas."""
        if self._has_explicit_room_areas(text):
            return None
        n = None
        m = re.search(r'(\d)\s*[-]?\s*bhk', text, re.IGNORECASE)
        if m:
            n = int(m.group(1))
        if n is None:
            for word, num in [('single',1),('one',1),('double',2),('two',2),('triple',3),('three',3),('four',4)]:
                if re.search(rf'\b{word}\s+bedroom', text, re.IGNORECASE):
                    n = num; break
        if n is None:
            m2 = re.search(r'(\d)\s+bedroom\s+(?:house|home|flat|apartment|villa)', text, re.IGNORECASE)
            if m2:
                n = int(m2.group(1))
        if n is None or n < 1 or n > 6:
            return None
        print(f"[BHK] Detected {n}BHK — expanding. Total: {total_sqm:.1f} sqm")
        order = ['living'] + ['bedroom']*n + ['kitchen','bathroom','bathroom','hallway','balcony']
        if n >= 2: order.append('study')
        if n >= 3: order.append('dining')
        ratios = {'living':0.22,'bedroom':0.20,'kitchen':0.12,'bathroom':0.07,'hallway':0.06,'balcony':0.07,'study':0.09,'dining':0.10}
        counts = {}
        for t in order: counts[t] = counts.get(t,0)+1
        total_w = sum(ratios.get(t,0.08)*counts[t] for t in set(order))
        inst = {}; rooms = []
        for t in order:
            inst[t] = inst.get(t,0)+1
            multi = counts[t]>1
            name = f"{t}_{inst[t]}" if multi else t
            area = max(self.min_areas.get(t,4), (ratios.get(t,0.08)/total_w)*total_sqm)
            rooms.append({'type':t,'instance':inst[t],'name':name,'area':round(area,2),'auto':False,'explicit_area':False})
        print(f"[BHK] {len(rooms)} rooms: " + ", ".join(r['name'] for r in rooms))
        return rooms

    def _try_fast_path(self, prompt: str) -> Optional[List[Dict]]:
        """
        Fast-path parser for structured prompts like:
        "Total = 1500 sqft. Hallway – 200 sqft; Dining – 200 sqft; Kitchen – 200 sqft; ..."
        Returns None if prompt doesn't match the structured pattern.
        """
        import re
        
        # Pattern: Room – Area sqft OR Room – Area sqm (labels may contain digits e.g. "Bedroom 1")
        pattern = re.compile(r'([A-Za-z][A-Za-z0-9\s]*?)\s*[-\u2013\u2014]\s*(\d+(?:\.\d+)?)\s*(sqft|sqm|m2)', re.IGNORECASE)
        matches = list(pattern.finditer(prompt))
        
        if not matches:
            return None
        
        # Extract total area
        total_match = re.search(r'Total\s*=?\s*(\d+(?:\.\d+)?)\s*(sqft|sqm|m2)', prompt, re.IGNORECASE)
        if not total_match:
            return None
        
        total_area_raw = float(total_match.group(1))
        total_units = total_match.group(2).lower()
        
        # Normalize to sqm for internal processing
        if total_units in ('sqft',):
            total_area = total_area_raw * 0.092903
        else:  # sqm, m2
            total_area = total_area_raw
        
        room_list = []
        for m in matches:
            room_label = m.group(1).strip()
            area_raw = float(m.group(2))
            units = m.group(3).lower()
            
            # Convert to sqm
            if units in ('sqft',):
                area_sqm = area_raw * 0.092903
            else:  # sqm, m2
                area_sqm = area_raw
            
            canonical = self._resolve_room_type(room_label)
            if canonical is None:
                print(f"[FastPath] Unknown room label '{room_label}', skipping")
                continue
            
            room_list.append({
                'type':          canonical,
                'area':          area_sqm,
                'auto':          False,
                'explicit_area': True,
            })
        
        if not room_list:
            return None
        
        print(f"[FastPath] Parsed {len(room_list)} rooms from structured format")
        return room_list

    def _resolve_room_type(self, label: str) -> str:
        """Resolve a human-readable label (e.g. 'Study Room') to canonical type."""
        label_lower = label.lower().strip()
        for canonical, synonyms in self.synonym_map.items():
            for syn in synonyms:
                if syn in label_lower or label_lower.startswith(syn):
                    return canonical
        return None
    # ────────────────────────────────────────────────────────────────────────────────

    def generate_blueprint(self, prompt: str) -> List[Dict[str, any]]:
        print(f"\n--- PROMPT: \"{prompt}\" ---")
        fast_path_result = self._try_fast_path(prompt)
        if fast_path_result is not None:
            total_area = sum(r['area'] for r in fast_path_result)
            room_list = fast_path_result
            used_area = total_area
            excluded_types = set()
        else:
            # Stage 2: BHK shorthand
            units_d    = self._detect_units(prompt.lower())
            raw_total  = self._extract_total_area(prompt.lower())
            total_sqm  = int(raw_total * 0.092903) if (units_d == 'sqft' or (units_d is None and raw_total > 400)) else raw_total
            bhk = self._parse_bhk_shorthand(prompt, total_sqm)
            if bhk is not None:
                room_list = bhk
                total_area = total_sqm
                used_area = sum(r['area'] for r in room_list)
                excluded_types = set()
            else:
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
        
        # NEW: Only auto-add essentials if user gave < 85% of total area explicitly
        explicit_coverage = sum(r['area'] for r in room_list if not r['auto']) / total_area if total_area > 0 else 0
        AUTO_ADD_THRESHOLD = 0.85
        
        missing_essentials = []
        if explicit_coverage < AUTO_ADD_THRESHOLD:
            missing_essentials = [t for t in essential_types if t not in existing_types]
        
        # We process this BEFORE calculating remaining area so they get a share
        if missing_essentials:
            print(f"Auto-adding missing essentials: {missing_essentials} (coverage: {explicit_coverage:.1%})")
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
        else:
            print(f"Skipping auto-add of essentials (coverage: {explicit_coverage:.1%} ≥ {AUTO_ADD_THRESHOLD:.1%})")
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
                    "living":   0.28,
                    "bedroom":  0.31,
                    "kitchen":  0.12,
                    "dining":   0.10,
                    "bathroom": 0.08,
                    "hallway":  0.07,
                    "storage":  0.04,
                    "study":    0.08,
                    "balcony":  0.06,
                    "garden":   0.06,
                    "parking":  0.06,
                }
                
                # Rule 2: "bigger [room]" -> 1.5x default percentage
                amplified_rooms = set()
                for t in self.room_types:
                    for syn in self.synonym_map.get(t, [t]):
                        if re.search(r'\b(bigger|larger|huge|massive|large)\s+' + re.escape(syn) + r'\b', prompt.lower()):
                            amplified_rooms.add(t)
                # Rule 27: "X should be bigger than Y" → amplify X
                comp_re = re.compile(
                    r'(\w[\w\s]{1,15}?)\s+(?:should\s+be\s+|must\s+be\s+)?'
                    r'(?:bigger|larger|much\s+bigger|much\s+larger)\s+than',
                    re.IGNORECASE
                )
                for cm in comp_re.finditer(prompt.lower()):
                    t_big = self._resolve_room_type(cm.group(1).strip())
                    if t_big:
                        amplified_rooms.add(t_big)

                # Rule 28: "small/tiny/compact [room]" OR "[room] should be small" → 0.5x default
                shrunk_rooms = set()
                for t in self.room_types:
                    for syn in self.synonym_map.get(t, [t]):
                        # "small study", "tiny bathroom", "compact kitchen"
                        if re.search(r'\b(small|tiny|compact|minimal)\s+' + re.escape(syn) + r'\b', prompt.lower()):
                            shrunk_rooms.add(t)
                        # "study should be small", "keep the study small"
                        if re.search(re.escape(syn) + r'\s+(?:should\s+be\s+|must\s+be\s+|be\s+)?(?:small|tiny|compact|minimal)\b', prompt.lower()):
                            shrunk_rooms.add(t)

                adjusted_defaults = {}
                for t, pct in DEFAULTS.items():
                    if t in amplified_rooms:
                        adjusted_defaults[t] = pct * 1.5
                    elif t in shrunk_rooms:
                        adjusted_defaults[t] = pct * 0.5
                    else:
                        adjusted_defaults[t] = pct
                
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
                    
        # 3. Final Scaling to match Total Area exactly (only auto-assigned rooms)
        current_total = sum(r['area'] for r in room_list)
        if current_total > 0 and abs(current_total - total_area) > 1:
            # Separate explicit and auto-assigned rooms
            explicit_total = sum(r['area'] for r in room_list if r.get('explicit_area', False))
            auto_total = current_total - explicit_total
            
            if auto_total > 0:
                remaining_budget = total_area - explicit_total
                scale = remaining_budget / auto_total if auto_total > 0 else 1.0
                # Rule 26: guard negative or absurd scale
                if scale <= 0 or scale > 5.0:
                    scale = max(0.1, min(5.0, scale))
                print(f"Scaling auto-assigned rooms by {scale:.2f} to fit {total_area} (explicit: {explicit_total}, auto: {auto_total})")
                for r in room_list:
                    if not r.get('explicit_area', False):
                        r['area'] = max(self.min_areas.get(r['type'], 5), int(r['area'] * scale))
            else:
                print(f"All rooms have explicit areas, no scaling needed")

        # Rule 17: if total still way over budget, proportionally scale all down
        final_total = sum(r['area'] for r in room_list)
        if final_total > total_area * 1.5:
            s = total_area / final_total
            for r in room_list:
                r['area'] = max(self.min_areas.get(r['type'], 4), int(r['area'] * s))

        # Rule 11: final naming pass — every room gets instance + name
        type_final: Dict[str, int] = {}
        for r in room_list:
            type_final[r['type']] = type_final.get(r['type'], 0) + 1
        type_cur2: Dict[str, int] = {}
        for r in room_list:
            t = r['type']
            type_cur2[t] = type_cur2.get(t, 0) + 1
            r['instance'] = type_cur2[t]
            r['name'] = f"{t}_{type_cur2[t]}" if type_final[t] > 1 else t

        return room_list