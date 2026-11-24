"""Deterministic Russian normalization and conservative query interpretation."""
import re
import unicodedata

ALIASES = {'ул': 'улица', 'пр': 'проспект', 'просп': 'проспект', 'пркт': 'проспект',
           'пер': 'переулок', 'ш': 'шоссе', 'наб': 'набережная', 'бул': 'бульвар',
           'пл': 'площадь', 'акад': 'академика', 'д': 'дом', 'корп': 'корпус', 'стр': 'строение',
           'авт': 'автобус', 'кам': 'камера', 'спецтехника': 'техника'}
CUES = {'автобус': 'bus', 'маршрут': 'bus', 'камера': 'camera', 'техника': 'equipment',
        'задание': 'task', 'событие': 'event', 'база': 'base', 'снегоприемник': 'snow_site',
        'снегоприемная': 'snow_site', 'участок': 'road_section', 'адрес': 'address'}
STOP = {'улица', 'проспект', 'переулок', 'шоссе', 'дом', 'номер', 'площадка'}


def normalize(text):
    text = unicodedata.normalize('NFKC', text.replace('№', ' ')).casefold().replace('ё', 'е')
    text = re.sub(r'пр\s*-\s*(?:кт|т)', 'проспект', text)
    text = re.sub(r'(?<=\d)\s*/\s*(?=\d)', '/', text)
    return ' '.join(ALIASES.get(t, t) for t in re.findall(r'[^\W_]+(?:/[^\W_]+)*', text))


def terms(text):
    return tuple(t for t in normalize(text).split() if t not in STOP)


def interpret(query):
    all_terms = terms(query)
    explicit = next((CUES[t] for t in all_terms if t in CUES), None)
    content = tuple(t for t in all_terms if t not in CUES)
    numeric = any(any(c.isdigit() for c in t) for t in content)
    intent = explicit or ('address' if numeric and any(t.isalpha() for t in content)
                          else 'ambiguous_number' if content and all(t.isdigit() for t in content)
                          else 'text')
    return content, intent, explicit


def edit_distance(a, b):
    """Optimal string alignment distance; includes adjacent transpositions."""
    prev2 = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            score = min(row[j-1]+1, prev[j]+1, prev[j-1]+(ca != cb))
            if prev2 is not None and j > 1 and ca == b[j-2] and a[i-2] == cb:
                score = min(score, prev2[j-2]+1)
            row.append(score)
        prev2, prev = prev, row
    return prev[-1]
