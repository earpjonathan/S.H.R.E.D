"""Build the output template: a minimal .docx carrying an example file's styles.xml.

Output pasted into Verbatim then uses the same style definitions as the examples
(Heading 4 tags, StyleUnderline, Emphasis, Style13ptBold), so it looks identical.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from cardcutter.ooxml import W, W_NS

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>"""

SETTINGS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="{W_NS}">
<w:defaultTabStop w:val="720"/>
<w:characterSpacingControl w:val="doNotCompress"/>
<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>
</w:settings>"""

DEFAULT_SECT = (
    f'<w:sectPr xmlns:w="{W_NS}"><w:pgSz w:w="12240" w:h="15840"/>'
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
    "</w:sectPr>"
)


def empty_document(sect_pr: bytes | None = None) -> bytes:
    sect = sect_pr or DEFAULT_SECT.encode()
    doc = etree.fromstring(f'<w:document xmlns:w="{W_NS}"><w:body/></w:document>'.encode())
    doc.find(f"{W}body").append(etree.fromstring(sect))
    return etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)


def build_template(source_docx: Path, out_path: Path) -> Path:
    """Copy styles.xml and the page setup (sectPr) from `source_docx` into a blank .docx."""
    with zipfile.ZipFile(source_docx) as z:
        styles = z.read("word/styles.xml")
        doc = etree.fromstring(z.read("word/document.xml"))
    sect = doc.find(f"{W}body/{W}sectPr")
    sect_bytes = None
    if sect is not None:
        # drop header/footer references: the template has no header/footer parts
        for ref in list(sect):
            if ref.tag in (f"{W}headerReference", f"{W}footerReference"):
                sect.remove(ref)
        sect_bytes = etree.tostring(sect)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/document.xml", empty_document(sect_bytes))
        z.writestr("word/styles.xml", styles)
        z.writestr("word/settings.xml", SETTINGS)
    return out_path
