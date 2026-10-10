"""Who may see and change what.

* A project is shared by its members. Everything in it (conversations, items, people, documents, conflicts,
  history) is common to all of them, and every change is logged with the member's name.
* A conversation belongs to the person who had it. Its owner controls it live (stop, delete, choose the project);
  once it is filed under a project, every member of that project can see it.
* Personal things (nudge settings, auto-listen rules, what Sync learned, unfiled conversations, personal
  documents) are visible to their owner only.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .events import record
from .models import Item, Project, ProjectMember, User
from .models import Session as SessionRow


def project_ids(db: Session, user_id: int) -> set[int]:
    return set(db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)))


def member_ids(db: Session, project_id: int | None) -> set[int]:
    if project_id is None:
        return set()
    return set(db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)))


def is_member(db: Session, user_id: int, project_id: int | None) -> bool:
    return project_id is not None and db.get(ProjectMember, (project_id, user_id)) is not None


def require_project(db: Session, user: User, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None or not is_member(db, user.id, project_id):
        raise HTTPException(404, "project not found")
    return p


def can_view_session(db: Session, user_id: int, row: SessionRow) -> bool:
    return row.user_id == user_id or is_member(db, user_id, row.project_id)


def require_session(db: Session, user: User, session_id: int, owner: bool = False) -> SessionRow:
    row = db.get(SessionRow, session_id)
    if row is None or not can_view_session(db, user.id, row):
        raise HTTPException(404, "conversation not found")
    if owner and row.user_id != user.id:
        raise HTTPException(403, "only the person who had this conversation can do that")
    return row


def require_item(db: Session, user: User, item_id: int) -> Item:
    it = db.get(Item, item_id)
    if it is None:
        raise HTTPException(404, "item not found")
    if it.project_id is not None:
        if not is_member(db, user.id, it.project_id):
            raise HTTPException(404, "item not found")
    else:
        row = db.get(SessionRow, it.session_id) if it.session_id else None
        if row is None or row.user_id != user.id:
            raise HTTPException(404, "item not found")
    return it


def audience(db: Session, row: SessionRow) -> set[int]:
    """Who should hear about a conversation's progress: its owner plus the members of its project."""
    return {row.user_id} | member_ids(db, row.project_id)


def add_member(db: Session, project_id: int, user_id: int, added_by: int | None) -> bool:
    if db.get(ProjectMember, (project_id, user_id)) is not None:
        return False
    db.add(ProjectMember(project_id=project_id, user_id=user_id, added_by=added_by))
    user = db.get(User, user_id)
    record(db, "member.added", actor="user", entity="user", entity_id=user_id, project_id=project_id,
           user_id=added_by if added_by is not None else user_id, after={"name": user.name if user else ""})
    return True


def user_names(db: Session, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
