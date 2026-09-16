import os, re, sys

base = r'agent_bridge\session_4\requests'
for d in sorted(os.listdir(base)):
    p = os.path.join(base, d)
    if not os.path.isdir(p):
        continue
    if os.path.exists(os.path.join(p, 'response.json')):
        continue
    pd = os.path.join(p, 'prompt_dynamic.txt')
    if not os.path.exists(pd):
        print(d, '(no prompt_dynamic yet)')
        continue
    t = open(pd, encoding='utf-8', errors='replace').read()
    names = re.findall(r'"student_name"\s*:\s*"([^"]+)"', t)
    if not names:
        names = re.findall(r'student_name["\s:]+([^\s,"}]+)', t)
    qs = sorted(set(re.findall(r'Q1[23]\(P\d\)', t)))
    b = re.search(r'"batch_index"\s*:\s*(\d+)', t) or re.search(r'[批次batch_]+(\d+)', t)
    print(d, 'batch', b.group(1) if b else '?', qs, names)
