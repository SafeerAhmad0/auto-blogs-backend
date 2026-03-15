path = r"C:\Users\safee\Documents\AI Automates\auto-blogs\main.py"
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
print(f"Total lines: {len(lines)}")
with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines[:288])
print("Done. File truncated to 288 lines.")
