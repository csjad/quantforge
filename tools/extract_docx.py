# 提取 docx 全文为 markdown（段落保留标题层级，表格转 markdown 表格）
# 用法: python extract_docx.py <input.docx> <output.md>
import sys
from docx import Document
from docx.oxml.ns import qn


def cell_text(tc):
    texts = []
    for p in tc.findall(qn("w:p")):
        t = "".join(node.text or "" for node in p.iter(qn("w:t")))
        if t.strip():
            texts.append(t.strip())
    return " ".join(texts).replace("|", "／")


def main():
    src, out = sys.argv[1], sys.argv[2]
    doc = Document(src)
    lines = []
    body = doc.element.body

    def handle_table(tbl):
        rows = []
        for tr in tbl.findall(qn("w:tr")):
            rows.append([cell_text(tc) for tc in tr.findall(qn("w:tc"))])
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines.append("| " + " | ".join(rows[0]) + " |")
        lines.append("|" + "---|" * width)
        for r in rows[1:]:
            lines.append("| " + " | ".join(r) + " |")
        lines.append("")

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            text = "".join(node.text or "" for node in child.iter(qn("w:t"))).strip()
            if not text:
                continue
            style = ""
            pPr = child.find(qn("w:pPr"))
            if pPr is not None:
                pStyle = pPr.find(qn("w:pStyle"))
                if pStyle is not None:
                    style = pStyle.get(qn("w:val")) or ""
            if style.lower().startswith("heading"):
                level = style.replace("heading", "").replace("Heading", "")
                try:
                    lv = int(level)
                except ValueError:
                    lv = 1
                lines.append("#" * min(lv, 6) + " " + text)
            else:
                lines.append(text)
        elif child.tag == qn("w:tbl"):
            handle_table(child)

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"OK lines={len(lines)} chars={sum(len(l) for l in lines)}")


if __name__ == "__main__":
    main()
