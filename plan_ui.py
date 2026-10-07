"""Public task plan state. Completion is explicit, never guessed from call count."""
import base64
import html
import json
import re

MARKER = re.compile(r'\[\[plan:([A-Za-z0-9+/=]+)\]\]')
STEP_MARKER = re.compile(r'\[\[planstep:(\d+)\]\]')

class Plan:
    def __init__(self, steps=()):
        self.steps = [{'text':str(s)[:400],'status':'pending'} for s in steps[:10]]
        self.active = None
    def select(self, step):
        if not isinstance(step,int) or isinstance(step,bool) or not 1 <= step <= len(self.steps):
            return None
        self.active=step
        if self.steps[step-1]['status']!='done':
            self.steps[step-1]['status']='running'
        return step
    def update(self, step, status):
        if status not in {'pending','running','done','blocked'} or self.select(step) is None:
            return False
        self.steps[step-1]['status']=status
        return True
    def marker(self):
        value=base64.b64encode(json.dumps(self.steps,ensure_ascii=False).encode()).decode()
        return '[[plan:'+value+']]'

def render_marker(match, commands=None):
    try:
        steps=json.loads(base64.b64decode(match[1]))
        rows=[]
        icons={'pending':'○','running':'◐','done':'✓','blocked':'!'}
        labels={'pending':'Pending','running':'In progress','done':'Done','blocked':'Blocked'}
        for i,step in enumerate(steps[:10],1):
            status=step.get('status','pending')
            if status not in icons: status='pending'
            rows.append('<li class="plan-row '+status+'"><span class="plan-icon" aria-hidden="true">'+icons[status]+'</span><div><span class="plan-label">Step '+str(i)+' · '+labels[status]+'</span><div>'+html.escape(str(step.get('text','')),quote=True)+'</div>'+(commands or {}).get(i,'')+'</div></li>')
        return '<section class="task-plan" aria-label="Task plan"><div class="plan-title">Plan</div><ol>'+''.join(rows)+'</ol></section>'
    except (ValueError,TypeError,KeyError):
        return ''

def replace(text,plan):
    """Keep one checklist updated in every full-response snapshot."""
    if MARKER.search(text):
        return MARKER.sub(lambda _:plan.marker(),text)
    return text

def render(text, commands=None):
    text=MARKER.sub(lambda m:render_marker(m,commands),text)
    return STEP_MARKER.sub(lambda m:'<div class="plan-command-label">Commands for step '+m[1]+'</div>',text)


_NUMBER_WORDS = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}


def requested_steps(request):
    """Extract only an explicit checklist count and title list, not arbitrary quotes."""
    match = re.search(r"(?:exactly\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)[ -]+(?:short[ -]+)?(?:step(?:s)?|titles?)\b", request, re.I)
    if not match:
        return None
    if match.start() > 0 and request[match.start()-1] in "\"\'":
        return None
    token = match[1].lower()
    count = int(token) if token.isdigit() else _NUMBER_WORDS[token]
    if not 1 <= count <= 10:
        return None
    tail = request[match.end():]
    title_match = re.search(r"(?:named|called|titled|titles?\s*:)\s*", tail, re.I)
    # 'two short titles: ...' already identifies the list boundary.
    if title_match:
        tail = tail[title_match.end():]
    elif re.match(r"\s*:", tail) and 'title' in match[0].lower():
        tail = tail.lstrip()[1:]
    else:
        return [f"Complete requested step {i}" for i in range(1, count+1)]
    titles = []
    for _ in range(count):
        item = re.match(r"\s*(?:and\s+|,\s*)?[\"']([^\"'\n]+)[\"']", tail, re.I)
        if not item:
            return [f"Complete requested step {i}" for i in range(1, count+1)]
        titles.append(item[1].strip())
        tail = tail[item.end():]
    return titles


def fallback_analysis(request):
    steps = requested_steps(request) or ["Complete requested task"]
    return ("**Restate:** " + request[:200] + "\n\n**Goal:** Complete the user's request\n\n"
            + "**Plan:**\n" + "\n".join(f"{i}. {title}" for i,title in enumerate(steps,1)))


def align_analysis(analysis, request):
    steps = requested_steps(request)
    if not steps:
        return analysis
    if all(title.startswith('Complete requested step ') for title in steps):
        import agentic
        if len(agentic.parse_plan(analysis)) == len(steps):
            return analysis
    section = re.search(r"(?im)^\s*\*\*Plan:\*\*[^\n]*\n", analysis)
    if not section:
        return analysis + "\n\n**Plan:**\n" + "\n".join(f"{i}. {title}" for i,title in enumerate(steps,1))
    rest = analysis[section.end():]
    end = re.search(r"(?m)^\s*\*\*[A-Z][^\n]*?:\*\*", rest)
    after = rest[end.start():] if end else ''
    return analysis[:section.end()] + "\n".join(f"{i}. {title}" for i,title in enumerate(steps,1)) + "\n\n" + after
