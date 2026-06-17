with open('scripts/validation_runner.py', 'r', encoding='utf-8') as f:
    c = f.read()

# Fix JSON open
c = c.replace(
    "with open(_WATCHLIST_PATH) as f:",
    'with open(_WATCHLIST_PATH, encoding="utf-8") as f:'
)

with open('scripts/validation_runner.py', 'w', encoding='utf-8') as f:
    f.write(c)
print('Klaar')