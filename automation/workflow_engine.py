"""Bounded declarative workflow runner. No shell, eval, network, or implicit scheduling."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class WorkflowStep:
    name: str
    action: str
    argument: str = ""
    requires_approval: bool = True

class WorkflowEngine:
    ALLOWED_ACTIONS={"note","wait_for_approval","emit"}
    MAX_STEPS=20
    def __init__(self, handlers=None, approver=None):
        self.handlers=dict(handlers or {})
        self.approver=approver
    def run(self, steps):
        if not isinstance(steps,(list,tuple)) or not 1<=len(steps)<=self.MAX_STEPS: raise ValueError("Workflow must contain 1-20 steps.")
        results=[]
        for index,step in enumerate(steps):
            if isinstance(step,dict):
                name,action,arg,approval=step.get("name",f"step-{index+1}"),step.get("action"),step.get("argument",""),step.get("requires_approval",True)
            elif isinstance(step,WorkflowStep): name,action,arg,approval=step.name,step.action,step.argument,step.requires_approval
            else: raise ValueError("Each workflow step must be a mapping or WorkflowStep.")
            if not isinstance(name,str) or not name.strip() or not isinstance(arg,str) or len(arg)>2000 or not isinstance(approval,bool): raise ValueError("Invalid workflow step fields.")
            if action not in self.ALLOWED_ACTIONS: raise ValueError(f"Action {action!r} is not allow-listed.")
            if approval and (self.approver is None or self.approver(name,action,arg) is not True):
                results.append({"step":name,"ok":False,"status":"approval_denied"}); break
            if action=="wait_for_approval": results.append({"step":name,"ok":True,"status":"approved"})
            else:
                handler=self.handlers.get(action)
                if handler is None: results.append({"step":name,"ok":True,"status":"recorded","output":arg[:500]})
                else:
                    try: output=handler(arg)
                    except Exception: results.append({"step":name,"ok":False,"status":"handler_failed"}); break
                    results.append({"step":name,"ok":True,"status":"completed","output":str(output)[:1000]})
        return results
