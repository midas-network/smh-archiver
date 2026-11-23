"""Minimal Markdown-subset to HTML rendering shared by the README PDF and the HTML pages."""
import html
import re


def basic_markdown_to_html(markdown_text: str) -> str:
    """Render the generated README Markdown subset for the PDF convenience copy."""

    blocks: list[str] = []
    paragraph: list[str] = []
    in_list = False
    lines = markdown_text.splitlines()
    index = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(
                f"<p>{_markdown_inline(' '.join(item.strip() for item in paragraph))}</p>"
            )
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            blocks.append("</ul>")
            in_list = False

    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            close_list()
            index += 1
            continue
        if _looks_like_markdown_table(lines, index):
            flush_paragraph()
            close_list()
            table_lines = [lines[index], lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            blocks.append(_markdown_table_to_html(table_lines))
            continue
        if stripped.startswith("#"):
            marker, _, heading = stripped.partition(" ")
            if marker and set(marker) == {"#"}:
                flush_paragraph()
                close_list()
                level = min(len(marker), 4)
                blocks.append(f"<h{level}>{_markdown_inline(heading.strip())}</h{level}>")
                index += 1
                continue
        if stripped.startswith("- "):
            flush_paragraph()
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append(f"<li>{_markdown_inline(stripped[2:].strip())}</li>")
            index += 1
            continue
        paragraph.append(line)
        index += 1

    flush_paragraph()
    close_list()
    return "\n".join(blocks)


def _looks_like_markdown_table(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and lines[index].strip().startswith("|")
        and _is_markdown_table_delimiter(lines[index + 1])
    )


def _is_markdown_table_delimiter(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = _split_markdown_table_row(stripped)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _split_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def _markdown_table_to_html(lines: list[str]) -> str:
    headers = _split_markdown_table_row(lines[0])
    rows = [_split_markdown_table_row(line) for line in lines[2:]]
    columns = ""
    if "Result data path inside archive" in headers:
        # Filenames need more room than the short type and row-count columns.
        widths = [14, 22, 10, 42, 12] if len(headers) == 5 else [24, 12, 50, 14]
        columns = "<colgroup>" + "".join(
            f'<col style="width: {width}%">' for width in widths
        ) + "</colgroup>"
    html_rows = [
        "<table>",
        columns,
        "<thead><tr>"
        + "".join(f"<th>{_markdown_inline(header)}</th>" for header in headers)
        + "</tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        padded = [*row, *([""] * (len(headers) - len(row)))]
        html_rows.append(
            "<tr>"
            + "".join(f"<td>{_markdown_inline(cell)}</td>" for cell in padded[: len(headers)])
            + "</tr>"
        )
    html_rows.extend(["</tbody>", "</table>"])
    return "\n".join(html_rows)


def _markdown_inline(text: str) -> str:
    pieces = text.split("`")
    rendered: list[str] = []
    for index, piece in enumerate(pieces):
        if index % 2:
            rendered.append(f"<code>{html.escape(piece)}</code>")
        else:
            rendered.append(_markdown_links_to_html(piece))
    return "".join(rendered)


def _markdown_links_to_html(text: str) -> str:
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    rendered: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        rendered.append(html.escape(text[cursor : match.start()]))
        label, url = match.groups()
        if re.match(r"\s*(javascript|data|vbscript):", url, re.IGNORECASE):
            rendered.append(html.escape(label))
        else:
            rendered.append(
                f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
            )
        cursor = match.end()
    rendered.append(html.escape(text[cursor:]))
    return "".join(rendered)
