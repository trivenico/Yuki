"""Thin CLI entry point over Yuki's shared provider, model and readiness services."""
from __future__ import annotations
import argparse, json, os
from collections.abc import Callable, Sequence
from core.config import load_config
from core.readiness import build_readiness_report
from core.model_manager import get_hardware_report, get_model_catalog
from core.yuki_ai import YukiAI
from core.vector_memory import VectorMemory
from providers.factory import create_provider

def build_parser():
    parser=argparse.ArgumentParser(prog="yuki",description="Yuki AI assistant")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor",help="Report local runtime readiness")
    sub.add_parser("provider-status",help="Check configured provider")
    sub.add_parser("ai-status",help="Show Yuki AI application-service status")
    plan=sub.add_parser("plan",help="Create a bounded, inspectable plan"); plan.add_argument("goal")
    sub.add_parser("hardware",help="Show local read-only hardware inventory")
    models=sub.add_parser("models",help="Inspect model families and hardware-aware recommendations")
    models.add_argument("--task",choices=("general","coding","reasoning","multilingual","research","tool_use","multimodal","on_device"),default="general")
    ask=sub.add_parser("ask",help="Send one prompt to configured provider"); ask.add_argument("prompt")
    sub.add_parser("chat",help="Start interactive chat without tool execution")
    memory=sub.add_parser("memory",help="Manage local Yuki memory explicitly")
    memory_sub=memory.add_subparsers(dest="memory_action",required=True)
    memory_add=memory_sub.add_parser("add",help="Save a memory note"); memory_add.add_argument("text"); memory_add.add_argument("--category",choices=("note","preference","lesson"),default="note")
    memory_search=memory_sub.add_parser("search",help="Search local memories"); memory_search.add_argument("query")
    memory_list=memory_sub.add_parser("list",help="List recent local memories"); memory_list.add_argument("--limit",type=int,default=20)
    memory_delete=memory_sub.add_parser("delete",help="Delete one local memory by ID"); memory_delete.add_argument("memory_id")
    return parser

def _write_json(value,output_fn=print):
    output_fn(json.dumps(value,indent=2,ensure_ascii=False,sort_keys=True))

def _safe_ask(provider,prompt):
    if not isinstance(prompt,str) or not prompt.strip(): return "Yuki: Please enter a message.",2
    try:
        if not provider.is_available(): return "Yuki: The configured provider is unavailable.",3
        answer=provider.generate(prompt.strip())
    except Exception as exc: return f"Yuki: Provider request failed ({type(exc).__name__}).",4
    if not isinstance(answer,str) or not answer.strip(): return "Yuki: Provider returned an invalid response.",4
    return answer,0

def run_cli(argv:Sequence[str]|None=None,*,config_loader:Callable=load_config,provider_factory:Callable=create_provider,readiness_builder:Callable=build_readiness_report,hardware_reporter:Callable=get_hardware_report,model_catalog:Callable=get_model_catalog,input_fn:Callable[[str],str]=input,output_fn:Callable[[str],object]=print):
    args=build_parser().parse_args(argv)
    if args.command=="doctor": _write_json(readiness_builder().to_dict(),output_fn); return 0
    if args.command=="hardware":
        try: _write_json(hardware_reporter(),output_fn); return 0
        except Exception as exc: output_fn(f"Hardware inspection failed ({type(exc).__name__})."); return 4
    if args.command=="models":
        try: _write_json(model_catalog(task=args.task),output_fn); return 0
        except Exception as exc: output_fn(f"Model catalog unavailable ({type(exc).__name__})."); return 4
    if args.command=="memory":
        try:
            memory=VectorMemory(os.environ.get("YUKI_MEMORY_PATH","yuki_memory.json"))
            if args.memory_action=="add": result=memory.remember(args.text,category=args.category)
            elif args.memory_action=="search": result=memory.search(args.query)
            elif args.memory_action=="list": result=memory.store.list(limit=args.limit)
            elif args.memory_action=="delete": result={"deleted":memory.store.delete(args.memory_id)}
            else: return 2
            _write_json(result,output_fn); return 0
        except Exception as exc:
            output_fn(f"Memory operation failed ({type(exc).__name__})."); return 4
    try: provider=provider_factory(config_loader())
    except Exception as exc: output_fn(f"Yuki could not initialize the configured provider ({type(exc).__name__})."); return 2
    memory = None
    if os.environ.get("YUKI_ENABLE_MEMORY","").strip().lower() in {"1","true","yes"}:
        memory = VectorMemory(os.environ.get("YUKI_MEMORY_PATH","yuki_memory.json"))
    ai=YukiAI(
        provider,
        memory_search=(lambda query: memory.search(query)) if memory else None,
        memory_store=(lambda question, answer: memory.remember(
            f"User: {question}\nYuki: {answer}", category="lesson"
        )) if memory else None,
    )
    if args.command=="ai-status":
        status=ai.status(); _write_json(status,output_fn); return 0 if status["provider_available"] else 3
    if args.command=="plan":
        try:
            plan=ai.plan(args.goal)
            _write_json({"goal":plan.goal,"memory_context":list(plan.memory_context),"steps":[{"action":s.action,"description":s.description,"requires_approval":s.requires_approval} for s in plan.steps]},output_fn)
            return 0
        except Exception as exc:
            output_fn(f"Planning failed ({type(exc).__name__})."); return 4
    if args.command=="provider-status":
        try: available=bool(provider.is_available())
        except Exception: available=False
        _write_json({"provider":type(provider).__name__,"available":available,"status":"ready" if available else "unavailable"},output_fn)
        return 0 if available else 3
    if args.command=="ask":
        try:
            if not provider.is_available(): output_fn("Yuki: The configured provider is unavailable."); return 3
            answer=ai.answer(args.prompt); output_fn(answer); return 0
        except Exception as exc:
            output_fn(f"Yuki: AI request failed ({type(exc).__name__})."); return 4
    if args.command=="chat":
        output_fn("Yuki chat. Type /quit to exit. Tool execution is disabled in this CLI mode.")
        while True:
            try: message=input_fn("You> ")
            except (EOFError,KeyboardInterrupt): output_fn(""); return 0
            if message.strip().lower() in {"/quit","/exit"}: return 0
            if not message.strip(): continue
            try:
                if not provider.is_available(): output_fn("Yuki: The configured provider is unavailable."); return 3
                answer=ai.answer(message); output_fn("Yuki> "+answer)
            except Exception as exc:
                output_fn(f"Yuki: AI request failed ({type(exc).__name__})."); return 4
    return 0

def main():
    try: return run_cli()
    except SystemExit as exc: return int(exc.code or 0)

if __name__=="__main__": raise SystemExit(main())
