"""`kb-sync` command line (contracts/kb-cli.md, FR-031).

Parses every command and switch, loads config, and dispatches lazily to the story module
that implements it. Errors print as `ERROR: <message>` on stderr; stdout carries results.
"""

import argparse
import importlib
import sys

from . import KbSyncError, config

# command -> module whose run(cfg, args) implements it
MODULES = {
    "pull": "kb_sync.pull",
    "ingest": "kb_sync.ingest",
    "ask": "kb_sync.ask",
    "pack": "kb_sync.packs",
    "notebook": "kb_sync.notebooklm",
    "review": "kb_sync.review",
    "eval": "kb_sync.evalset",
    "mcp": "kb_sync.mcp_server",
    "nightly": "kb_sync.nightly",
}
AUTH_MODULES = {"google": "kb_sync.sources.drive", "appflowy": "kb_sync.sources.appflowy"}


def build_parser():
    p = argparse.ArgumentParser(prog="kb-sync", description="Personal knowledge pipeline (spec 002).")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    a = sub.add_parser("auth", help="sign in to a source (re-run to renew)")
    a.add_argument("source", choices=sorted(AUTH_MODULES))

    pl = sub.add_parser("pull", help="pull configured sources into the store")
    pl.add_argument("--source", choices=["drive", "appflowy", "research"])

    i = sub.add_parser("ingest", help="bring LightRAG up to date (run via scripts/kb.sh ingest)")
    mode = i.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="write the ingest plan")
    mode.add_argument("--apply", metavar="PLAN_ID", help="apply a plan")
    i.add_argument("--backup", metavar="ARCHIVE", help="archive taken in this run (required for removals)")
    i.add_argument("--confirm-removals", action="store_true", help="perform held removals")

    q = sub.add_parser("ask", help="cited answer from the knowledge base")
    q.add_argument("question")
    q.add_argument("--local", action="store_true", help="answer with the local model; nothing leaves the machine")
    q.add_argument("--no-filter", action="store_true", help="send the unfiltered context to the answer stage")

    pk = sub.add_parser("pack", help="build or refresh a topic's research pack")
    pk.add_argument("topic")

    nb = sub.add_parser("notebook", help="NotebookLM commands")
    nb_sub = nb.add_subparsers(dest="notebook_command", required=True, metavar="command")
    push = nb_sub.add_parser("push", help="push a pack to its notebook")
    push.add_argument("topic")

    r = sub.add_parser("review", help="propose or apply a notebook review")
    r.add_argument("topic")
    r.add_argument("--apply", action="store_true", help="apply the approved proposal")
    r.add_argument("--web", action="store_true", help="add candidate web sources via Claude web search")

    e = sub.add_parser("eval", help="run the FR-025 test set")
    e.add_argument("--filter", choices=["none", "reranker", "local", "all"], default="all")

    sub.add_parser("mcp", help="stdio MCP server for Claude Code")
    sub.add_parser("nightly", help="write the nightly run summary")
    return p


def _dispatch(cfg, args):
    if args.command == "auth":
        module_name, fn = AUTH_MODULES[args.source], "auth"
    else:
        module_name, fn = MODULES[args.command], "run"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name != module_name:
            raise
        raise KbSyncError(f"`{args.command}` is not built yet") from None
    return getattr(module, fn)(cfg, args)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = config.load()
        return int(_dispatch(cfg, args) or 0)
    except KbSyncError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.exit_code
