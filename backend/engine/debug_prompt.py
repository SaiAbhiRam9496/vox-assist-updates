import sys
import os
import json

# Add project root to sys.path
project_root = r'c:\dev\VoxAssist\Project'
backend_path = os.path.join(project_root, 'backend')
if backend_path not in sys.path:
    sys.path.append(backend_path)

from engine.text_to_specs_v2 import ProximityLayoutGenerator

def debug_prompt():
    generator = ProximityLayoutGenerator()
    prompt = "Design a 1200 sqft house with 3 bedrooms, 2 bathrooms, a modular kitchen, and a small balcony"
    
    print(f"DEBUGGING PROMPT: {prompt}")
    result = generator.generate_blueprint(prompt)
    
    print("\n--- GENERATED ROOMS ---")
    for room in result:
        print(f"Room: {room['name']}, Type: {room['type']}, Area: {room['area']}sqm")
    
    if generator.last_metadata:
        print("\n--- METADATA ---")
        print(json.dumps(generator.last_metadata, indent=2))

if __name__ == "__main__":
    debug_prompt()
