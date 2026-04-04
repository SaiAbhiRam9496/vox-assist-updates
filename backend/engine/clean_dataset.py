import json
import os

def clean_json_dataset(input_path, output_path):
    if not os.path.exists(input_path):
        print(f"File {input_path} not found.")
        return

    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # The file contains multiple JSON objects like:
    # { ... }
    # { ... }
    # We need to split them. A simple way if they start with { is to tracking braces
    # or just split by "}\n{" which is common in this file format.
    
    objects = []
    current_obj = ""
    brace_count = 0
    
    for char in content:
        current_obj += char
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                # Finished one object
                try:
                    obj = json.loads(current_obj.strip())
                    objects.append(obj)
                except json.JSONDecodeError as e:
                    print(f"Error decoding object: {e}")
                current_obj = ""

    with open(output_path, 'w', encoding='utf-8') as f:
        for obj in objects:
            f.write(json.dumps(obj) + '\n')
            
    print(f"Cleaned {len(objects)} objects and saved to {output_path}")

if __name__ == "__main__":
    clean_json_dataset('dataset.json', 'dataset_cleaned.jsonl')
