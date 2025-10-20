import json
from collections import deque
with open('state_food.json', encoding='utf-8') as f:
    state = json.load(f)
navs=set()
queue=deque([state])
while queue:
    node=queue.popleft()
    if isinstance(node, dict):
        nav=node.get('navigationURL') or node.get('navigationUrl')
        if isinstance(nav, str) and nav.startswith('/cat/Food/'):
            navs.add(nav.split('?')[0])
        queue.extend(node.values())
    elif isinstance(node, list):
        queue.extend(node)
print('nav count', len(navs))
print('sample', list(sorted(navs))[:20])
