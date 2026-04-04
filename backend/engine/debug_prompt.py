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
    prompt = (
        "I want to design a modern 2BHK house of around 1200 sqft with a spacious living room (about 250 sqft) "
        "near the entrance, a kitchen (around 120 sqft) connected to a dining area, two bedrooms including a "
        "master bedroom (~200 sqft) with an attached bathroom and a guest bedroom (~150 sqft), along with a "
        "different common bathroom. I also want a small study/workspace and a balcony attached to the living room."
    )
    
    # This calls _ollama_parse internally
    result = generator.generate_blueprint(prompt)
    
    output_path = os.path.join(os.path.dirname(__file__), 'debug_final.txt')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"DEBUGGING PROMPT:\n{prompt}\n\n")
        f.write("--- RAW METADATA (from LLM) ---\n")
        f.write(json.dumps(generator.last_metadata, indent=2))
        f.write("\n\n--- PROCESSED ROOMS ---\n")
        for room in result:
            f.write(f"{room['name']}: {room['area']} sqm (auto={room['auto']})\n")
    
    print(f"Debug saved to {output_path}")

if __name__ == "__main__":
    debug_prompt()
