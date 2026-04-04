import sys
import os

# Add engine to path
sys.path.append(os.path.join(os.getcwd(), "backend", "engine"))
sys.path.append(os.path.join(os.getcwd(), "backend"))

from engine.text_to_specs_v2 import ProximityLayoutGenerator
from services.generation_service import GenerationService
import asyncio

async def test_text_mode_standardization():
    service = GenerationService()
    # Prompt with specific area
    prompt = "Design a 1200 sqft house with 3 bedrooms and a kitchen"
    
    print("\n--- Testing Text Mode Standardization (1200 sqft) ---")
    result = await service.generate_layout(prompt=prompt)
    
    best_candidate = max(result["candidates"], key=lambda x: x["score"])
    candidate_spec = best_candidate["spec"]
    
    print(f"Resulting Rooms: {[r['type'] for r in candidate_spec['rooms']]}")
    built_area = sum(r["area"] for r in candidate_spec["rooms"])
    print(f"Total Built Area: {built_area} sqft")
    
    assert abs(built_area - 1200) < 50, f"Expected ~1200, got {built_area}"
    print("✅ Text mode end-to-end test passed!")

if __name__ == "__main__":
    asyncio.run(test_text_mode_standardization())
