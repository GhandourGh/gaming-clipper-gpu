"""The camera-path expression must be equal to the old linear chain but shallow enough for ffmpeg's parser."""
import random
import re
import sys

from clipper.reframe import path_expr


def _linear(keys):  # the previous implementation, kept here as the reference
    f = lambda x: f"{x:.4f}".rstrip("0").rstrip(".") or "0"  # noqa: E731
    expr = f(keys[-1][1])
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        seg = f(v1) if t1 - t0 < 0.003 else f"{f(v0)}+({f(v1 - v0)})*(t-{f(t0)})/{f(t1 - t0)}"
        expr = f"if(lt(t,{f(t1)}),{seg},{expr})"
    return f"if(lt(t,{f(keys[0][0])}),{f(keys[0][1])},{expr})"


def _py(expr: str, t: float) -> float:
    """Evaluate the tiny grammar our expressions use: numbers, t, + - * /, parentheses, lt(a,b) and if(c,x,y)."""
    pos = 0

    def peek() -> str:
        return expr[pos] if pos < len(expr) else ""

    def num_or_name():
        nonlocal pos
        m = re.compile(r"\d+\.?\d*|[a-z]+").match(expr, pos)
        pos = m.end()
        return m.group(0)

    def primary():
        nonlocal pos
        if peek() == "(":
            pos += 1
            v = add()
            pos += 1
            return v
        tok = num_or_name()
        if tok == "t":
            return t
        if peek() == "(":  # function call
            pos += 1
            args = [add()]
            while peek() == ",":
                pos += 1
                args.append(add())
            pos += 1
            if tok == "lt":
                return 1.0 if args[0] < args[1] else 0.0
            return args[1] if args[0] else args[2]
        return float(tok)

    def unary():
        nonlocal pos
        if peek() == "-":
            pos += 1
            return -unary()
        return primary()

    def mul():
        nonlocal pos
        v = unary()
        while peek() in ("*", "/"):
            op = peek()
            pos += 1
            r = unary()
            v = v * r if op == "*" else v / r
        return v

    def add():
        nonlocal pos
        v = mul()
        while peek() in ("+", "-"):
            op = peek()
            pos += 1
            r = mul()
            v = v + r if op == "+" else v - r
        return v

    return add()


def _depth(expr: str) -> int:
    d = m = 0
    for c in expr:
        d += c == "("
        m = max(m, d)
        d -= c == ")"
    return m


def test_same_values_as_the_linear_chain_and_shallow():
    sys.setrecursionlimit(50000)  # the old deeply nested form needs it to be evaluated at all
    random.seed(3)
    for n in (2, 3, 7, 64, 400):
        ts = sorted(random.sample(range(0, 20000), n))
        keys = [(round(t / 100, 3), round(random.random(), 4)) for t in ts]
        new, old = path_expr(keys), _linear(keys)
        for t in [keys[0][0] - 1, keys[0][0], keys[-1][0], keys[-1][0] + 5] + [random.uniform(keys[0][0], keys[-1][0]) for _ in range(60)]:
            assert abs(_py(new, t) - _py(old, t)) < 1e-6, (n, t)
        if n >= 64:
            assert _depth(new) < 40 < _depth(old)
