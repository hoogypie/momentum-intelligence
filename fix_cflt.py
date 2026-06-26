import json
with open('research/validation_watchlist.json', encoding='utf-8') as f:
    d = json.load(f)
for g in d['groups']:
    before = len(g['tickers'])
    g['tickers'] = [t for t in g['tickers'] if t['ticker'] != 'CFLT']
    if len(g['tickers']) < before:
        print(f'CFLT verwijderd uit {g["id"]}')
with open('research/validation_watchlist.json', 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
print('Klaar')