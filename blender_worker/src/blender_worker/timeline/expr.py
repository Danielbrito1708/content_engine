"""Tiny expression language for timeline positions.

See `docs/edicao_declarativa.md` § "As três ideias que fazem o formato
funcionar" for the rationale — this is "tempo simbólico" made concrete.

Grammar (informal)::

    expr := term (('+' | '-') term)*
    term := NUMBER UNIT              # a duration literal: "0.3s", "4f"
          | 'timeline_end'
          | '$' NAME                 # an already-resolved anchor
          | NAME '(' expr (',' expr)* ')'   # after(...), max(...), min(...)
          | '(' expr ')'

`$name` inside `after(...)` is special-cased to mean an *input*'s duration
(`after($hook)`); everywhere else `$name` means an already-resolved anchor.
Both resolve to frame counts in the same "relative to timeline zero" space —
see `resolver.py`'s module docstring for why nothing here ever adds the
Blender +1 frame origin.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(
    r"""
    (?:
        (?P<number>\d+(?:\.\d+)?)(?P<unit>[sf])
      | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
      | \$(?P<ref>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>[()+,-])
    )
    """,
    re.VERBOSE,
)


class ExprError(ValueError):
    pass


def _tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ExprError(f"unreadable expression near {text[pos:pos + 20]!r} in {text!r}")
        pos = m.end()
        if m.group("number"):
            tokens.append(("duration", (float(m.group("number")), m.group("unit"))))
        elif m.group("ident"):
            tokens.append(("ident", m.group("ident")))
        elif m.group("ref"):
            tokens.append(("ref", m.group("ref")))
        else:
            tokens.append((m.group("op"), m.group("op")))
    tokens.append(("end", None))
    return tokens


class _Parser:
    def __init__(self, tokens, text):
        self.tokens = tokens
        self.i = 0
        self.text = text

    def _peek(self):
        return self.tokens[self.i]

    def _next(self):
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def parse(self):
        node = self._expr()
        if self._peek()[0] != "end":
            raise ExprError(f"unexpected trailing input in {self.text!r}")
        return node

    def _expr(self):
        node = self._term()
        while self._peek()[0] in ("+", "-"):
            op, _ = self._next()
            rhs = self._term()
            node = ("binop", op, node, rhs)
        return node

    def _term(self):
        kind, value = self._next()
        if kind == "duration":
            return ("duration", value)
        if kind == "ref":
            return ("ref", value)
        if kind == "ident":
            if value == "timeline_end":
                return ("timeline_end",)
            if self._peek()[0] != "(":
                raise ExprError(f"{value!r} is not a known keyword in {self.text!r}")
            self._next()  # consume '('
            args = [self._expr()]
            while self._peek()[0] == ",":
                self._next()
                args.append(self._expr())
            if self._next()[0] != ")":
                raise ExprError(f"missing ')' after {value}(...) in {self.text!r}")
            return ("call", value, args)
        if kind == "(":
            node = self._expr()
            if self._next()[0] != ")":
                raise ExprError(f"missing ')' in {self.text!r}")
            return node
        raise ExprError(f"unexpected token {value!r} in {self.text!r}")


def parse(text: str):
    """Parse a time expression into an AST. Raises `ExprError` on bad syntax."""
    return _Parser(_tokenize(text), text).parse()


def duration_to_frames(value: float, unit: str, frame_rate: int) -> int:
    return round(value * frame_rate) if unit == "s" else round(value)


def evaluate(node, *, anchors, inputs, frame_rate, timeline_end=None):
    """Evaluate an AST to a frame count, relative to timeline zero.

    `anchors` — already-resolved anchor values (relative frames), looked up
    by bare `$name`. `inputs` — `{name: duration_frames}`, looked up only via
    `after(name)`; a name absent or `None` (an optional input the caller did
    not supply) raises, so a template referencing a missing asset fails at
    resolution instead of producing a bad render. `timeline_end` — only
    passed in during the bed pass; `None` makes any use of the keyword
    raise, which is what stops a content track from depending on the length
    it is still helping to decide.
    """
    kind = node[0]
    if kind == "duration":
        value, unit = node[1]
        return duration_to_frames(value, unit, frame_rate)
    if kind == "ref":
        name = node[1]
        if name not in anchors:
            raise ExprError(f"unknown reference ${name}")
        return anchors[name]
    if kind == "timeline_end":
        if timeline_end is None:
            raise ExprError(
                "timeline_end is only available while resolving bed tracks — "
                "a content track or anchor cannot depend on the length it helps decide"
            )
        return timeline_end
    if kind == "binop":
        _, op, lhs, rhs = node
        left = evaluate(lhs, anchors=anchors, inputs=inputs, frame_rate=frame_rate, timeline_end=timeline_end)
        right = evaluate(rhs, anchors=anchors, inputs=inputs, frame_rate=frame_rate, timeline_end=timeline_end)
        return left + right if op == "+" else left - right
    if kind == "call":
        _, name, args = node
        if name == "after":
            if len(args) != 1 or args[0][0] != "ref":
                raise ExprError("after(...) takes a single $input reference")
            input_name = args[0][1]
            if input_name not in inputs or inputs[input_name] is None:
                raise ExprError(f"after(${input_name}) — input {input_name!r} was not supplied")
            return inputs[input_name]
        values = [
            evaluate(a, anchors=anchors, inputs=inputs, frame_rate=frame_rate, timeline_end=timeline_end)
            for a in args
        ]
        if name == "max":
            return max(values)
        if name == "min":
            return min(values)
        raise ExprError(f"unknown function {name}(...)")
    raise ExprError(f"unhandled node {node!r}")


def resolve(text: str, **kwargs) -> int:
    """Parse and evaluate `text` in one call."""
    return evaluate(parse(text), **kwargs)
