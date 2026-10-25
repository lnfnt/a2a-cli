"""v1.0.0 §4 I-03: cookbook of scenarios."""
from __future__ import annotations

import shlex

from a2a.cli import build_parser


def _recipes(out):
    data = out.get("data", out) if isinstance(out, dict) else {}
    for key in ("recipes", "items", "entries", "cookbook"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, list):
            return val
    return []


def test_cookbook_has_eight_recipes(run):
    code, out, err = run("cookbook")
    assert code == 0, (out, err)
    recipes = _recipes(out)
    assert isinstance(recipes, list)
    assert len(recipes) == 8


def test_cookbook_commands_parse(run):
    code, out, err = run("cookbook")
    assert code == 0, (out, err)
    recipes = _recipes(out)
    parser = build_parser()
    cmds = 0
    for r in recipes:
        for key in ("commands", "steps", "lines", "command_list"):
            val = r.get(key) if isinstance(r, dict) else None
            if isinstance(val, list):
                cmds_list = val
                break
        else:
            cmds_list = []
        assert cmds_list, r
        for c in cmds_list:
            s = c.get("command") if isinstance(c, dict) else c
            s = c.get("cmd") if (isinstance(c, dict) and "cmd" in c and "command" not in c) else s
            s = c.get("line") if (isinstance(c, dict) and "line" in c and "command" not in c and "cmd" not in c) else s
            argv = shlex.split(str(s))
            if argv and argv[0] == "a2a":
                argv = argv[1:]
            parser.parse_args(argv)
            cmds += 1
    assert cmds >= 8