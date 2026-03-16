import sys
path = r'C:\Users\safee\Documents\AI Automates\auto-blog-frontend\app\agents\page.tsx'
lines = open(path, encoding='utf-8').readlines()
print(f'Total lines: {len(lines)}')
open(path, 'w', encoding='utf-8').writelines(lines[:220])
print('Done. Truncated to 220 lines')
