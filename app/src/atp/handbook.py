"""Visual usage guide with escaped, progressively disclosed reference chapters."""

import html
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .handbook_content import FAQ, STEPS


def _chapter_html(chapter):
    body, code = [], False
    for line in chapter.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            body.append("</code></pre>" if code else "<pre><code>")
            code = not code
        elif code:
            body.append(html.escape(line) + "\n")
        elif line.startswith("# "):
            body.append("<h3>" + html.escape(line[2:]) + "</h3>")
        elif line.startswith("## "):
            body.append("<h4>" + html.escape(line[3:]) + "</h4>")
        elif line.strip():
            body.append("<p>" + html.escape(line) + "</p>")
    if code:
        body.append("</code></pre>")
    return "".join(body)


def render_handbook(source_dir: Path, *, operator=False, memory_enabled=False):
    chapters = sorted(source_dir.glob("[0-9][0-9]-*.md"))
    if not operator:
        chapters = [p for p in chapters if p.name.startswith(("00-", "01-"))]
    references = [
        {"title": p.read_text(encoding="utf-8").splitlines()[0].lstrip("# "), "body": _chapter_html(p)}
        for p in chapters
    ]
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape()
    )
    return env.get_template("handbook.html").render(
        references=references, operator=operator, memory_enabled=memory_enabled, steps=STEPS, faq=FAQ
    )
