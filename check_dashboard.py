path = r'C:\Users\safee\Documents\AI Automates\auto-blog-frontend\app\dashboard\page.tsx'
lines = open(path, encoding='utf-8').readlines()
print(f'Dashboard total lines: {len(lines)}')
# Print lines 260-end
for i, l in enumerate(lines[260:], start=261):
    print(i, l.rstrip())
