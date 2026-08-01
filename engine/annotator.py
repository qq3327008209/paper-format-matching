"""Annotator: add Word comments to .docx files marking format errors."""

import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from typing import List, Dict, Optional

from lxml import etree

logger = logging.getLogger(__name__)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"
COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
COMMENTS_REL_TYPE = f"{R_NS}/comments"
AUTHOR = "格式检查器"
INITIALS = "CHK"


def annotate_document(
    src_path: str,
    dst_path: str,
    errors: List[Dict],
    max_per_paragraph: int = 3,
) -> bool:
    """Copy a source file and add Word-style comments for format errors.

    Args:
        src_path: Source document path (``.docx``).
        dst_path: Output document path (``.docx``).
        errors: Error list from ``RuleEngine.validate()``.
        max_per_paragraph: Max comments per paragraph.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    lower = src_path.lower()
    if lower.endswith(".docx"):
        return _annotate_docx(src_path, dst_path, errors, max_per_paragraph)
    # .doc / .pdf: not supported for in-place annotation
    logger.warning("Annotator only supports .docx; skipping %s", src_path)
    return False


def _annotate_docx(
    src_path: str,
    dst_path: str,
    errors: List[Dict],
    max_per_paragraph: int,
) -> bool:
    """Core .docx annotation: copy file, inject comment XML."""
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    shutil.copy(src_path, dst_path)

    annotatable: list[tuple[int, str, str]] = []
    for err in errors:
        idx = err.get("paragraph_index")
        desc = (err.get("description") or "").strip()
        if idx is not None and desc:
            annotatable.append((int(idx), desc, err.get("type", "format")))

    if not annotatable:
        logger.info("No annotatable errors for %s", src_path)
        return True

    para_to_errors: Dict[int, List[tuple]] = {}
    for idx, desc, etype in annotatable:
        para_to_errors.setdefault(idx, []).append((desc, etype))

    try:
        return _write_comments(dst_path, para_to_errors, max_per_paragraph)
    except Exception as exc:
        logger.error("Annotation failed %s: %s", src_path, exc)
        return False


def _write_comments(
    docx_path: str,
    para_to_errors: Dict[int, List[tuple]],
    max_per_paragraph: int,
) -> bool:
    """Inject four-piece comment XML into the .docx ZIP."""
    with zipfile.ZipFile(docx_path, "r") as z:
        names = z.namelist()
        doc_xml = z.read("word/document.xml")
        has_comments = "word/comments.xml" in names
        comments_xml = z.read("word/comments.xml") if has_comments else None
        ct_xml = z.read("[Content_Types].xml")
        rels_xml = z.read("word/_rels/document.xml.rels")
        other = {n: z.read(n) for n in names
                 if n not in ("word/document.xml", "[Content_Types].xml",
                              "word/_rels/document.xml.rels",
                              "word/comments.xml")}

    parser = etree.XMLParser(remove_blank_text=False)
    doc_tree = etree.fromstring(doc_xml, parser)
    ct_tree = etree.fromstring(ct_xml, parser)
    rels_tree = etree.fromstring(rels_xml, parser)
    comments_tree = (
        etree.fromstring(comments_xml, parser)
        if comments_xml is not None
        else etree.Element(f"{W}comments", nsmap={"w": W_NS})
    )

    # Next comment ID
    existing_ids = set()
    for c in comments_tree.findall(f"{W}comment"):
        cid = c.get(f"{W}id")
        if cid is not None:
            try:
                existing_ids.add(int(cid))
            except ValueError:
                pass
    next_id = max(existing_ids) + 1 if existing_ids else 0

    # Register content type
    if not has_comments:
        override = etree.SubElement(ct_tree, f"{CT_NS}Override")
        override.set("PartName", "/word/comments.xml")
        override.set("ContentType", COMMENTS_CT)

    # Register relationship
    if not has_comments:
        existing_rids = [
            r.get("Id") for r in rels_tree.findall(f"{PR_NS}Relationship")
        ]
        rid = "rIdCmt1"
        i = 1
        while rid in existing_rids:
            i += 1
            rid = f"rIdCmt{i}"
        rel = etree.SubElement(rels_tree, f"{PR_NS}Relationship")
        rel.set("Id", rid)
        rel.set("Type", COMMENTS_REL_TYPE)
        rel.set("Target", "comments.xml")

    body = doc_tree.find(f"{W}body")
    if body is None:
        logger.warning("No w:body in document")
        return False
    paragraphs = body.findall(f"{W}p")

    added = 0
    for pid, err_list in para_to_errors.items():
        if pid >= len(paragraphs) or pid < 0:
            continue
        p = paragraphs[pid]
        pPr = p.find(f"{W}pPr")
        prev_end = None

        for desc, etype in err_list[:max_per_paragraph]:
            range_start = etree.Element(f"{W}commentRangeStart")
            range_start.set(f"{W}id", str(next_id))

            if prev_end is not None:
                prev_end.addnext(range_start)
            elif pPr is not None:
                pPr.addnext(range_start)
            else:
                p.insert(0, range_start)

            range_end = etree.SubElement(p, f"{W}commentRangeEnd")
            range_end.set(f"{W}id", str(next_id))

            ref_run = etree.SubElement(p, f"{W}r")
            rpr = etree.SubElement(ref_run, f"{W}rPr")
            rstyle = etree.SubElement(rpr, f"{W}rStyle")
            rstyle.set(f"{W}val", "CommentReference")
            ref = etree.SubElement(ref_run, f"{W}commentReference")
            ref.set(f"{W}id", str(next_id))

            comment_el = etree.SubElement(comments_tree, f"{W}comment")
            comment_el.set(f"{W}id", str(next_id))
            comment_el.set(f"{W}author", AUTHOR)
            comment_el.set(f"{W}initials", INITIALS)
            comment_el.set(f"{W}date",
                           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            cp = etree.SubElement(comment_el, f"{W}p")
            cr = etree.SubElement(cp, f"{W}r")
            ct = etree.SubElement(cr, f"{W}t")
            full_text = f"[{etype}] {desc}"
            ct.text = full_text
            ct.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

            prev_end = range_end
            next_id += 1
            added += 1

    logger.info("Added %d comments to %s", added, docx_path)

    tmp = docx_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("word/document.xml",
                       etree.tostring(doc_tree, xml_declaration=True,
                                      encoding="UTF-8", standalone=True))
        zout.writestr("[Content_Types].xml",
                       etree.tostring(ct_tree, xml_declaration=True,
                                      encoding="UTF-8", standalone=True))
        zout.writestr("word/_rels/document.xml.rels",
                       etree.tostring(rels_tree, xml_declaration=True,
                                      encoding="UTF-8", standalone=True))
        zout.writestr("word/comments.xml",
                       etree.tostring(comments_tree, xml_declaration=True,
                                      encoding="UTF-8", standalone=True))
        for name, content in other.items():
            zout.writestr(name, content)
    shutil.move(tmp, docx_path)
    return True
