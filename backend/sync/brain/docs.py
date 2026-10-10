"""Document ingest: sentence-window chunks (for claim checks / private questions) and a facts table
(numbers with direction, for the instant Promise Guard). Deterministic, no model needed."""
from __future__ import annotations

import io
import re

from sqlalchemy.orm import Session

from .. import textparse as tp
from ..embed import embedder, to_blob
from ..events import record
from ..guide import guide
from ..models import DocChunk, DocFact, Document

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(f"# Page {i + 1}\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages))


def _section_label(heading: str) -> str:
    m = re.match(r"^\s*(\d+(?:\.\d+)*)[.)]?\s", heading)
    return m.group(1) if m else heading.strip()[:40]


def sections(text: str) -> list[tuple[str, str]]:
    """(section label, paragraph) pairs, following markdown headings."""
    out, label, buf = [], "", []
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            if buf:
                out.append((label, " ".join(buf)))
                buf = []
            label = _section_label(m.group(1))
        elif line.strip():
            buf.append(line.strip())
        elif buf:
            out.append((label, " ".join(buf)))
            buf = []
    if buf:
        out.append((label, " ".join(buf)))
    return out


def chunks(text: str, max_words: int = 60) -> list[tuple[str, str]]:
    out = []
    for label, para in sections(text):
        window: list[str] = []
        for s in tp.sentences(para):
            if window and len(" ".join(window + [s]).split()) > max_words:
                out.append((label, " ".join(window)))
                window = []
            window.append(s)
        if window:
            out.append((label, " ".join(window)))
    return out


def ingest(db: Session, name: str, text: str, project_id: int | None, user_id: int | None,
           actor: str = "user") -> Document:
    """Store a document (shared with the project's members, or the uploader's own when ``project_id`` is None)."""
    doc = Document(name=name, text=text, project_id=project_id, user_id=user_id)
    db.add(doc)
    db.flush()
    parts = chunks(text)
    vecs = embedder.encode([c for _, c in parts])
    for (label, chunk), vec in zip(parts, vecs, strict=True):
        db.add(DocChunk(document_id=doc.id, section=label, text=chunk, vec=to_blob(vec)))
    slots = guide().slots
    n_facts = 0
    for label, para in sections(text):
        for s in tp.sentences(para):
            low = tp.words_to_digits(s).lower()
            for q in tp.quantities(s):
                q_pos = low.find(q.text.lower())
                best = None
                for sid, slot in slots.items():
                    if slot.kind != q.kind:
                        continue
                    for trig in slot.triggers:
                        m = tp.term_re(trig).search(low)
                        # The trigger must sit close to the number ("implementation takes 6-8 weeks").
                        if m and abs(m.start() - q_pos) <= 40 and (best is None or abs(m.start() - q_pos) < best[0]):
                            best = (abs(m.start() - q_pos), sid, slot)
                if best:
                    _, sid, slot = best
                    db.add(DocFact(document_id=doc.id, slot=sid, value=q.as_dict(), direction=slot.direction,
                                   text=s, section=label, vec=to_blob(embedder.encode1(s))))
                    n_facts += 1
    record(db, "document.added", actor=actor, user_id=user_id, entity="document", entity_id=doc.id,
           project_id=project_id, after={"name": name, "chunks": len(parts), "facts": n_facts})
    return doc
