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
