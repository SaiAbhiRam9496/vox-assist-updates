import sys
import os
import json

# Add project root to sys.path
project_root = r'c:\dev\VoxAssist\Project'
backend_path = os.path.join(project_root, 'backend')
if backend_path not in sys.path:
    sys.path.append(backend_path)

from engine.text_to_specs_v2 import ProximityLayoutGenerator

def test_parser():
    generator = ProximityLayoutGenerator()
    
    test_prompts = [
        "I want a 1500 sqft house with a large living room (400 sqft), a small kitchen (100 sqft), and two bedrooms. The kitchen should be next to the dining area. The bedrooms should be far from the living room.",
        "Design a 3BHK flat of 1200 sqft. Master bedroom adjacent to bathroom. Kitchen near balcony.",
        "A 2000 sqft house. Study near bedroom. Garden beside living room. Kitchen opposite to bathroom.",
        "Just a studio apartment, 500 sqft.",
        "I need a cozy home with two bedrooms, a nice kitchen, and a small garden. Around 1000 square feet.",
    ]
    
    output_path = os.path.join(os.path.dirname(__file__), 'test_results.txt')
    
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, prompt in enumerate(test_prompts, 1):
            f.write(f"\n{'='*60}\n")
            f.write(f"TEST {i}\n")
            f.write(f"PROMPT: {prompt}\n")
            f.write(f"{'='*60}\n")
            
            result = generator.generate_blueprint(prompt)
            metadata = generator.last_metadata
            
            f.write(f"\n[Parsed Rooms] ({len(result)} rooms):\n")
            total_parsed = 0
            for room in result:
                area = room['area']
                total_parsed += area
                f.write(f"  - {room['name']:20s}  {area:7.1f} sqm  (auto={room['auto']})\n")
            
            f.write(f"\n  Total parsed area: {total_parsed:.1f} sqm\n")
            
            if metadata:
                f.write(f"  Target total area: {metadata.get('total_area', 'N/A')} sqm\n")
                prefer = metadata.get('adjacency_prefer', [])
                avoid  = metadata.get('adjacency_avoid', [])
                f.write(f"\n[Adjacency Rules]:\n")
                f.write(f"  Prefer: {prefer if prefer else 'None'}\n")
                f.write(f"  Avoid:  {avoid if avoid else 'None'}\n")
            
            f.write(f"\n")
            f.flush()
            print(f"Test {i} done.")
    
    print(f"\nAll tests complete. Results saved to: {output_path}")

if __name__ == "__main__":
    test_parser()
