"""Database tables. Every state change is also written to ``events`` (see sync/events.py), with who made it."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """A person using Sync. Personal settings (nudges, auto-listen, what Sync learned) belong to the user;
    projects are shared with their members."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    nudge_mode: Mapped[str] = mapped_column(String(16), default="normal")  # quiet | normal | active
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en"])
    autolisten: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ApiToken(Base):
    """A key that signs a browser or a desktop agent in as a user. Only a hash is stored."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(60), default="browser")  # browser | desktop agent
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    name: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | closed
    brief: Mapped[str] = mapped_column(Text, default="")  # the project summary: where things stand
    invite_code: Mapped[str | None] = mapped_column(String(40), unique=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ProjectMember(Base):
    """Who shares a project. Everything in a project (calls, items, people, documents, history) is common."""

    __tablename__ = "project_members"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    added_by: Mapped[int | None] = mapped_column(Integer, default=None)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)


class Person(Base):
    """Someone outside the team who appears in a project's conversations (a client, a vendor…)."""

    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    org: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(120), default="")
    concerns: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Session(Base):
    """One conversation: a call, a meeting, a sample call or pasted text. ``user_id`` is whose conversation it was."""

    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True,
                                                   default=None)
    mode: Mapped[str] = mapped_column(String(16), default="online")  # online | in_person | script | paste
    status: Mapped[str] = mapped_column(String(16), default="live")  # detected | live | ended
    title: Mapped[str] = mapped_column(String(300), default="")
    app: Mapped[str] = mapped_column(String(120), default="")
    caller: Mapped[str] = mapped_column(String(200), default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    criteria: Mapped[list] = mapped_column(JSON, default=list)
    recap: Mapped[dict] = mapped_column(JSON, default=dict)
    recap_status: Mapped[str] = mapped_column(String(16), default="none")  # none | draft | refining | refined
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)


class Utterance(Base):
    __tablename__ = "utterances"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(8))  # you | them
    speaker: Mapped[str] = mapped_column(String(120), default="")
    text: Mapped[str] = mapped_column(Text)
    t_start: Mapped[float] = mapped_column(Float, default=0.0)
    t_end: Mapped[float] = mapped_column(Float, default=0.0)
    vec: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)


class Item(Base):
    """A typed piece of project memory: fact, question, concern, proposal, decision, commitment, note…

    ``speaker``/``owner`` of "me" mean the Sync user in ``user_id`` (whose call it came from, or who typed it).
    """

    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, default=None)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), index=True, default=None)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    type: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    speaker: Mapped[str] = mapped_column(String(120), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")  # "me", a person's name, or ""
    due: Mapped[str] = mapped_column(String(120), default="")
    condition: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    slot: Mapped[str] = mapped_column(String(64), default="")
    value: Mapped[dict] = mapped_column(JSON, default=dict)  # parsed quantity for slot facts
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # utterance ids
    quote: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded_by: Mapped[int | None] = mapped_column(Integer, default=None)
    created_by: Mapped[str] = mapped_column(String(16), default="user")  # t0 | t1 | t2 | agent | user
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    vec: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)


class Conflict(Base):
    __tablename__ = "conflicts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), default=None)
    item_a: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    item_b: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    slot: Mapped[str] = mapped_column(String(64), default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | resolved
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Nudge(Base):
    __tablename__ = "nudges"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    tier: Mapped[str] = mapped_column(String(4), default="t0")
    kind: Mapped[str] = mapped_column(String(16))  # ask | confirm | caution | fact
    gap_type: Mapped[str] = mapped_column(String(8))
    key: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(120), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    t: Mapped[float] = mapped_column(Float, default=0.0)  # seconds into the session
    latency: Mapped[float] = mapped_column(Float, default=0.0)  # trigger utterance end -> shown
    outcome: Mapped[str] = mapped_column(String(16), default="shown")  # shown | used | useful | dismissed | expired
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Document(Base):
    """A reference document (price list, policy, guide). With a project it is shared with the project's members;
    without one it is the uploader's own and applies to all of their conversations."""

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), default=None)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), default=None)
    name: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class DocChunk(Base):
    __tablename__ = "doc_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)
    vec: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)


class DocFact(Base):
    """A checkable claim from a document, e.g. 'standard go-live is 6-8 weeks' (slot=go_live, 42..56 days)."""

    __tablename__ = "doc_facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    slot: Mapped[str] = mapped_column(String(64), default="")
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    direction: Mapped[str] = mapped_column(String(8), default="exact")  # min | max | exact
    text: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(String(300), default="")
    vec: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)


class PlaybookEntry(Base):
    """What Sync has learned about one user's habits (shown to them, editable)."""

    __tablename__ = "playbook"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # habit | style | effective_question | avoid
    text: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class GapStat(Base):
    """Per-user outcome counts per gap type; drives the gate's learned weights."""

    __tablename__ = "gap_stats"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    gap_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    shown: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[int] = mapped_column(Integer, default=0)
    useful: Mapped[int] = mapped_column(Integer, default=0)
    dismissed: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    """Append-only history of every change: what, by whom (user and name at the time), when and why."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None)
    user_name: Mapped[str] = mapped_column(String(120), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    session_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    actor: Mapped[str] = mapped_column(String(16))  # user | agent | t0 | t1 | t2 | system
    type: Mapped[str] = mapped_column(String(48))
    entity: Mapped[str] = mapped_column(String(24), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, default=None)
    before: Mapped[dict | None] = mapped_column(JSON, default=None)
    after: Mapped[dict | None] = mapped_column(JSON, default=None)
    reason: Mapped[str] = mapped_column(Text, default="")
