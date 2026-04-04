import sys
import os

# Add engine to path
sys.path.append(os.path.join(os.getcwd(), "backend", "engine"))
sys.path.append(os.path.join(os.getcwd(), "backend"))

from engine.text_to_specs_v2 import ProximityLayoutGenerator
from services.generation_service import GenerationService
import asyncio

async def test_manual_mode_standardization():
    service = GenerationService()
    
    # EXACT FAILURE CASE FROM SCREENSHOT
    # Total area: 1000
    # Rooms: Living (300), Bedroom (150), Kitchen (120), Bathroom (50) -> Sum 620
    prompt = "A house with a total area of around 1000 sqft. It includes: a 300 sqft living room, a 150 sqft bedroom, a 120 sqft kitchen, a 50 sqft bathroom."
    rooms_spec = [
        {"type": "living", "area": 300},
        {"type": "bedroom", "area": 150},
        {"type": "kitchen", "area": 120},
        {"type": "bathroom", "area": 50}
    ]
    
    print("\n--- Testing Manual Mode Standardization (Fail Case: 620/1000) ---")
    
    # 1. Test Spec Generation
    result = await service.generate_layout(prompt=prompt, rooms_spec=rooms_spec)
    
    # 2. Check Candidate Spec
    best_candidate = max(result["candidates"], key=lambda x: x["score"])
    candidate_spec = best_candidate["spec"]
    
    print(f"Resulting Rooms in Spec: {[r['type'] for r in candidate_spec['rooms']]}")
    
    built_area = sum(r["area"] for r in candidate_spec["rooms"])
    print(f"Total Built Area: {built_area} sqft")
    
    # Check if hallway exists
    hallway = next((r for r in candidate_spec["rooms"] if "Hallway" in r["type"]), None)
    if hallway:
        print(f"Hallway found with area: {hallway['area']} sqft")
    else:
        print("❌ Hallway NOT found in final spec!")

    assert built_area >= 990, f"Expected coverage ~1000, got {built_area}"
    assert hallway is not None, "Hallway should have been added to fill the 380 sqft gap"
    
    print("✅ End-to-End manual test passed!")

if __name__ == "__main__":
    asyncio.run(test_manual_mode_standardization())
