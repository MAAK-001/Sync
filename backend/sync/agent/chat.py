"""Talk to Sync any time: questions about your projects, new details, briefs and drafts.

In a project's chat the model gets the project's complete memory (summary, every number, all items, people);
everywhere it also gets the most relevant search results. Then a small tool loop. Every tool acts as the
signed-in user, only on projects they are a member of, and every change is logged with their name.
Status-finalising edits (agreed/done) come back as confirmations for the user to approve.
During a live call the CPU belongs to the call, so answers are retrieval-only (instant).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable

from sqlalchemy import select

from .. import access
from ..brain import brief as brief_mod
from ..brain import memory as memory_mod
from ..config import settings
from ..db import session_scope
from ..embed import embedder, to_blob
from ..events import as_dict, changed, record
from ..live.signals import slot_value
from ..llm import llm
from ..models import Item, Person, Project, User
from .search import search

_pending: dict[str, tuple[int, str, Callable[[], dict]]] = {}  # confirmation id -> (user id, description, action)

FINAL_STATUSES = {"agreed", "done"}

TOOLS = [
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "Search everything Sync remembers (projects, past calls, documents). Use for any question about what was said or agreed.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "project_id": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_project", "description": "Get a project's brief: open questions, commitments, conflicts, people.",
        "parameters": {"type": "object", "properties": {"project_id": {"type": "integer"}}, "required": ["project_id"]}}},
    {"type": "function", "function": {
        "name": "create_project", "description": "Create a new project.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "objective": {"type": "string"}, "context": {"type": "string"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "add_item",
        "description": "Save a new detail to a project: a fact, requirement, question, commitment, decision, concern or note.",
        "parameters": {"type": "object", "properties": {
            "project_id": {"type": "integer"},
            "type": {"type": "string", "enum": ["fact", "requirement", "question", "commitment", "decision", "concern", "note"]},
            "text": {"type": "string"}, "owner": {"type": "string", "description": "'me' or a person's name"},
            "due": {"type": "string"}, "speaker": {"type": "string", "description": "who said it, if known"}},
            "required": ["project_id", "type", "text"]}}},
    {"type": "function", "function": {
        "name": "update_item", "description": "Change an existing item (e.g. mark a commitment done, add an answer).",
        "parameters": {"type": "object", "properties": {
            "item_id": {"type": "integer"}, "status": {"type": "string", "enum": ["open", "proposed", "conditional", "agreed", "done"]},
            "answer": {"type": "string"}, "due": {"type": "string"}, "owner": {"type": "string"}},
            "required": ["item_id"]}}},
    {"type": "function", "function": {
        "name": "add_person", "description": "Add a person to a project.",
        "parameters": {"type": "object", "properties": {
            "project_id": {"type": "integer"}, "name": {"type": "string"}, "org": {"type": "string"},
            "role": {"type": "string"}}, "required": ["project_id", "name"]}}},
]

SYSTEM = """You are Sync, the assistant and memory for {name}'s conversations and projects.
- Answer from PROJECT MEMORY, RELEVANT MEMORY or your tools. Cite sources in brackets, e.g. (Priya, 3 Oct).
- If the answer isn't there, say you don't know. Never guess numbers, dates or names.
- When the user gives you new information, save it with add_item (or add_person / create_project).
- Keep answers short and concrete. "you" in memory means {name}; other Sync users are teammates.
Projects (id: name): {projects}"""


def _project_id(db, user_id: int, args: dict) -> int:
    pid = int(args["project_id"])
    if db.get(Project, pid) is None or not access.is_member(db, user_id, pid):
        raise ValueError(f"project {pid} not found")
    return pid


def _tool(name: str, args: dict, user_id: int, reason: str) -> dict:
    with session_scope() as db:
        if name == "search_memory":
            pid = _project_id(db, user_id, args) if args.get("project_id") else None
            return {"results": search(db, args.get("query", ""), user_id, pid, k=6)}
        if name == "get_project":
            return brief_mod.brief(db, _project_id(db, user_id, args), user_id)
        if name == "create_project":
            p = Project(name=args["name"].strip(), objective=args.get("objective", ""), context=args.get("context", ""),
                        created_by=user_id)
            db.add(p)
            db.flush()
            record(db, "project.created", actor="agent", entity="project", entity_id=p.id, project_id=p.id,
                   user_id=user_id, after=as_dict(p), reason=reason)
            access.add_member(db, p.id, user_id, added_by=user_id)
            return {"created_project": p.id, "name": p.name}
        if name == "add_item":
            pid = _project_id(db, user_id, args)
            typ = args.get("type", "note")
            status = "agreed" if typ in ("commitment", "decision") else "open"
            text = args["text"].strip()
            slot = slot_value(text)
            it = Item(project_id=pid, user_id=user_id, type=typ, text=text, status=status,
                      owner=args.get("owner", ""), due=args.get("due", ""), speaker=args.get("speaker", "me"),
                      slot=slot[0] if slot else "", value=slot[1] if slot else {}, confirmed=True,
                      created_by="agent", confidence=0.9, vec=to_blob(embedder.encode1(text)))
            db.add(it)
            db.flush()
            record(db, "item.created", actor="agent", entity="item", entity_id=it.id, project_id=pid,
                   user_id=user_id, after=as_dict(it), reason=reason)
            return {"saved_item": it.id}
        if name == "add_person":
            pid = _project_id(db, user_id, args)
            person = db.scalar(select(Person).where(Person.project_id == pid, Person.name.ilike(args["name"].strip())))
            if person is None:
                person = Person(project_id=pid, name=args["name"].strip(), org=args.get("org", ""),
                                role=args.get("role", ""))
                db.add(person)
                db.flush()
                record(db, "person.added", actor="agent", entity="person", entity_id=person.id, project_id=pid,
                       user_id=user_id, after={"name": person.name, "org": person.org, "role": person.role},
                       reason=reason)
            return {"person": person.id}
    if name == "update_item":
        return _update_item(args, user_id, reason)
    return {"error": f"unknown tool {name}"}


def _update_item(args: dict, user_id: int, reason: str) -> dict:
    item_id = int(args["item_id"])

    def apply() -> dict:
        with session_scope() as db:
            it = db.get(Item, item_id)
            if it is None or it.project_id is None or not access.is_member(db, user_id, it.project_id):
                return {"error": "item not found"}
            before = as_dict(it)
            for f in ("status", "answer", "due", "owner"):
                if args.get(f):
                    setattr(it, f, args[f])
            it.confirmed = True
            b, a = changed(before, as_dict(it))
            record(db, "item.updated", actor="agent", entity="item", entity_id=it.id, project_id=it.project_id,
                   user_id=user_id, before=b, after=a, reason=reason)
            return {"updated_item": it.id, "changes": a}

    if args.get("status") in FINAL_STATUSES:
        cid = uuid.uuid4().hex[:10]
        _pending[cid] = (user_id, f"Mark item {item_id} as {args['status']}", apply)
        return {"needs_confirmation": cid, "description": _pending[cid][1]}
    return apply()


def confirm(cid: str, user_id: int) -> dict:
    entry = _pending.get(cid)
    if entry is None or entry[0] != user_id:
        return {"error": "nothing to confirm (expired?)"}
    _pending.pop(cid)
    return entry[2]()


def _context(user_id: int, project_id: int | None) -> tuple[str, str, str]:
    """(user's name, their projects, the open project's complete memory or "")."""
    with session_scope() as db:
        user = db.get(User, user_id)
        ids = access.project_ids(db, user_id)
        projects = "; ".join(f"{p.id}: {p.name}" for p in db.scalars(select(Project).where(Project.id.in_(ids))))
        memory = memory_mod.load(db, project_id, user_id, full=True)[2] if project_id else ""
    return (user.name if user else "the user"), projects or "(none yet)", memory


async def chat(messages: list[dict], user_id: int, project_id: int | None = None) -> AsyncIterator[dict]:
    """Yields events: {"type": "token"|"tool"|"confirm"|"sources"|"done", ...}. The caller checks membership of
    ``project_id``."""
    question = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    with session_scope() as db:
        hits = search(db, question, user_id, project_id, k=6) if question else []
    yield {"type": "sources", "sources": hits}

    if llm.live_sessions > 0 or not settings.llm_enabled:
        if hits:
            text = "From memory:\n" + "\n".join(f"- {h['text']} ({h['source']})" for h in hits[:4])
        else:
            text = "I couldn't find that in memory."
        yield {"type": "token", "text": text}
        yield {"type": "done"}
        return

    name, projects, project_memory = _context(user_id, project_id)
    memory = "\n".join(f"- [{h['kind']}] {h['text']} ({h['source']}; project: {h['project']})" for h in hits) or "(nothing relevant found)"
    system = SYSTEM.format(name=name, projects=projects)
    if project_memory:
        system += f"\n\nThis chat is about project {project_id}. PROJECT MEMORY (complete)\n{project_memory}"
    convo = [{"role": "system", "content": system}]
    convo += [m for m in messages[:-1] if m.get("role") in ("user", "assistant")][-8:]
    hint = f" (current project id: {project_id})" if project_id else ""
    convo.append({"role": "user", "content": f"RELEVANT MEMORY\n{memory}\n\nUSER{hint}: {question}"})

    for _round in range(4):
        content, calls = [], []
        async for chunk in llm.chat_stream(settings.chat_model, convo, tools=TOOLS, num_predict=500):
            if chunk.content:
                content.append(chunk.content)
                yield {"type": "token", "text": chunk.content}
            calls.extend(chunk.tool_calls)
        if not calls:
            break
        convo.append({"role": "assistant", "content": "".join(content), "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            try:
                result = _tool(name, args, user_id, reason=f"chat: {question[:200]}")
            except Exception as e:  # tool errors go back to the model, not the user
                result = {"error": str(e)}
            yield {"type": "tool", "name": name, "args": args, "result": result}
            if "needs_confirmation" in result:
                yield {"type": "confirm", "id": result["needs_confirmation"], "description": result["description"]}
            convo.append({"role": "tool", "tool_name": name, "content": json.dumps(result, default=str)[:4000]})
    yield {"type": "done"}
