from __future__ import annotations

import re

from ..schemas.joint_review import JointDraftSentenceView


SENTENCE_SPLIT = re.compile(r"([，,。！？!？；;\n])")
_SENTENCE_TEXT_NORMALIZE = re.compile(
    r"[\s，,。！？!？；;、：“”‘’《》〈〉【】（）()\[\]{}]+"
)


def split_draft_sentences(draft_text: str) -> list[JointDraftSentenceView]:
    """Split a draft into reviewable propositions without dropping punctuation."""
    parts = SENTENCE_SPLIT.split(draft_text)
    sentences: list[JointDraftSentenceView] = []
    current = ""
    cursor = 0
    for part in parts:
        current += part
        if part in "，,。！？!？；;\n":
            cleaned = current.strip()
            if cleaned:
                sentences.append(
                    JointDraftSentenceView(
                        id=f"ds-{cursor}",
                        text=cleaned,
                    )
                )
            current = ""
            cursor += 1
    cleaned = current.strip()
    if cleaned:
        sentences.append(
            JointDraftSentenceView(
                id=f"ds-{cursor}",
                text=cleaned,
            )
        )
    return sentences


def draft_sentence_text_matches(expected: str, actual: str) -> bool:
    """Whether a model echo of a draft sentence is acceptable.

    Empty output is tolerated only because the numbered sentence id is the
    authoritative binding; the program then persists the exact draft text.
    Non-empty output must match after ignoring whitespace/punctuation so a
    missing trailing comma does not silently detach a finding from its sentence.
    """
    if not actual:
        return True
    return _SENTENCE_TEXT_NORMALIZE.sub(
        "", expected
    ) == _SENTENCE_TEXT_NORMALIZE.sub("", actual)


# Backwards-compatible alias used by existing callers during the migration.
_split_draft_sentences = split_draft_sentences
