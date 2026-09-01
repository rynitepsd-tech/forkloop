"""SQL literal quoting and ``?`` parameter substitution shared by DbAccess and
the world generators. Portable across SQLite and MariaDB (docs/contracts.md §5)."""

from __future__ import annotations

from typing import Any, Sequence


def quote(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    s = str(v)
    if "\\" in s:
        # MariaDB treats backslash as an escape character, SQLite does not; refuse rather than guess.
        raise ValueError("backslashes are not portable across SQLite and MariaDB literals")
    return "'" + s.replace("'", "''") + "'"


def substitute(sql: str, params: Sequence[Any] = ()) -> str:
    """Replace each ``?`` outside string literals with a quoted literal."""
    out: list[str] = []
    it = iter(params)
    in_str = False
    i = 0
    n = len(sql)
    used = 0
    while i < n:
        c = sql[i]
        if in_str:
            out.append(c)
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_str = False
        elif c == "'":
            in_str = True
            out.append(c)
        elif c == "?":
            try:
                out.append(quote(next(it)))
            except StopIteration as e:
                raise ValueError("not enough parameters for SQL placeholders") from e
            used += 1
        else:
            out.append(c)
        i += 1
    if used != len(params):
        raise ValueError(f"{len(params)} parameters given but {used} placeholders found")
    return "".join(out)


def ident(name: str) -> str:
    """Validate an identifier (table/column) so it can be interpolated safely."""
    if not name or not all(ch.isalnum() or ch == "_" for ch in name) or name[0].isdigit():
        raise ValueError(f"unsafe SQL identifier {name!r}")
    return name


__all__ = ["quote", "substitute", "ident"]
