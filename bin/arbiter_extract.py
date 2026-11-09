"""Turn requirement attachments Claude cannot read itself (.docx, .xlsx) into plain text. Stdlib only.

Both formats are zip files of XML. Anything we cannot read raises ValueError with a reason for the human.
"""
import re
import zipfile
import xml.etree.ElementTree as ET

READ_DIRECT = {".md", ".txt", ".csv", ".json", ".yml", ".yaml", ".xml", ".sql", ".pdf",
               ".png", ".jpg", ".jpeg", ".gif", ".webp"}  # Claude's Read handles these
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_ROWS = 2000


def _xml(z, name):
    return ET.fromstring(z.read(name))


def _para(p):
    out = []
    for el in p.iter():
        if el.tag == W + "t" and el.text:
            out.append(el.text)
        elif el.tag in (W + "tab",):
            out.append("\t")
        elif el.tag in (W + "br", W + "cr"):
            out.append("\n")
    return "".join(out)


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        body = _xml(z, "word/document.xml").find(W + "body")
        lines = []
        for el in body if body is not None else []:
            if el.tag == W + "p":
                lines.append(_para(el))
            elif el.tag == W + "tbl":
                for tr in el.iter(W + "tr"):
                    cells = [" ".join(_para(p) for p in tc.iter(W + "p")).strip() for tc in tr.findall(W + "tc")]
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_text(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(t.text or "" for t in si.iter(S + "t")) for si in _xml(z, "xl/sharedStrings.xml")]
        rels = {r.get("Id"): r.get("Target") for r in _xml(z, "xl/_rels/workbook.xml.rels").iter(PR + "Relationship")}
        out = []
        for sh in _xml(z, "xl/workbook.xml").iter(S + "sheet"):
            target = rels.get(sh.get(R + "id"), "").lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            out.append(f"## Sheet: {sh.get('name')}")
            rows = list(_xml(z, target).iter(S + "row")) if target in names else []
            for row in rows[:MAX_ROWS]:
                vals = {}
                for c in row.iter(S + "c"):
                    t, v = c.get("t"), c.find(S + "v")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(S + "t"))
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    vals[_col(c.get("r") or "A")] = val.replace("\n", " ")
                if any(vals.values()):
                    out.append("| " + " | ".join(vals.get(i, "") for i in range(max(vals) + 1)) + " |")
            if len(rows) > MAX_ROWS:
                out.append(f"(ตัดเหลือ {MAX_ROWS} แถวจาก {len(rows)} แถว)")
            out.append("")
    return "\n".join(out).strip() + "\n"


def extract(path):
    """Text for a .docx/.xlsx, None if Claude reads the file directly, ValueError if nobody can read it."""
    ext = path.suffix.lower()
    if ext in READ_DIRECT:
        return None
    if ext not in (".docx", ".xlsx"):
        raise ValueError(f"ยังอ่านไฟล์ชนิด {ext or '(ไม่มีนามสกุล)'} ไม่ได้")
    try:
        return docx_text(path) if ext == ".docx" else xlsx_text(path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, IndexError) as e:
        raise ValueError(f"แปลงไฟล์ไม่สำเร็จ ({type(e).__name__}: {e})")
