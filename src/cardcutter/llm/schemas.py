"""JSON schemas for each stage.

Kept to the subset every provider enforces (types, properties, required,
additionalProperties: false; no numeric bounds). Bounds are checked in code.
Property order matters for constrained decoding: "plan" comes first so the model
states its reasoning before it picks indices.
"""
from __future__ import annotations

RANGE = {
    "type": "object",
    "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
    "required": ["start", "end"],
    "additionalProperties": False,
}

PARTIAL = {
    "type": "object",
    "properties": {"word": {"type": "integer"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
    "required": ["word", "start", "end"],
    "additionalProperties": False,
}

UNDERLINE = {
    "type": "object",
    "properties": {"plan": {"type": "string"}, "underline": {"type": "array", "items": RANGE}},
    "required": ["plan", "underline"],
    "additionalProperties": False,
}

HIGHLIGHT = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "highlight": {"type": "array", "items": RANGE},
        "partial": {"type": "array", "items": PARTIAL},
    },
    "required": ["plan", "highlight", "partial"],
    "additionalProperties": False,
}

EMPHASIS = {
    "type": "object",
    "properties": {"emphasis": {"type": "array", "items": RANGE}},
    "required": ["emphasis"],
    "additionalProperties": False,
}

TAG = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "tag": {"type": "string"},
        "underline": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["plan", "tag", "underline"],
    "additionalProperties": False,
}

READBACK = {
    "type": "object",
    "properties": {
        "grammatical": {"type": "integer"},
        "coherent": {"type": "integer"},
        "proves_tag": {"type": "integer"},
        "power_tag": {"type": "boolean"},
        "feedback": {"type": "string"},
    },
    "required": ["grammatical", "coherent", "proves_tag", "power_tag", "feedback"],
    "additionalProperties": False,
}

QUALS = {
    "type": "object",
    "properties": {
        "authors": {"type": "array", "items": {"type": "string"}},
        "quals": {"type": "array", "items": {"type": "string"}},
        "date": {"type": "string"},
        "publication": {"type": "string"},
    },
    "required": ["authors", "quals", "date", "publication"],
    "additionalProperties": False,
}

LOCATE = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}, "rationale": {"type": "string"}},
                "required": ["start", "end", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

SUGGEST = {
    "type": "object",
    "properties": {
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string"}, "where": {"type": "string"}, "claim": {"type": "string"}},
                "required": ["kind", "where", "claim"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["gaps"],
    "additionalProperties": False,
}

QUERIES = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["plan", "queries"],
    "additionalProperties": False,
}

SCREEN = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "supports": {"type": "boolean"},
                    "strength": {"type": "integer"},
                    "credible": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "supports", "strength", "credible", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

INTENT = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["endorses", "qualifies", "disputes"]},
        "author_conclusion": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "author_conclusion", "reason"],
    "additionalProperties": False,
}

CLAIMPARTS = {
    "type": "object",
    "properties": {"parts": {"type": "array", "items": {"type": "string"}}},
    "required": ["parts"],
    "additionalProperties": False,
}

CLAIMCHECK = {
    "type": "object",
    "properties": {
        "supports": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["supports", "reason"],
    "additionalProperties": False,
}

CLAIMCHECK_PARTS = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "words": {"type": "string"},
                    "shown": {"type": "string", "enum": ["yes", "partly", "no"]},
                },
                "required": ["n", "words", "shown"],
                "additionalProperties": False,
            },
        },
        "supports": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["parts", "supports", "reason"],
    "additionalProperties": False,
}

REPAIR = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "add": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["plan", "add"],
    "additionalProperties": False,
}

TRIM = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "keep": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["plan", "keep"],
    "additionalProperties": False,
}

RANK = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "order": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["plan", "order"],
    "additionalProperties": False,
}
