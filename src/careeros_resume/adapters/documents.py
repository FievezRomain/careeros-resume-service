from datetime import UTC, datetime
from hashlib import sha256
from importlib.resources import as_file, files
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from docx import Document
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate
from reportlab.platypus.doctemplate import LayoutError
from reportlab.platypus.flowables import Flowable

from careeros_resume.application.drafts import DraftInputInvalid
from careeros_resume.application.exports import RenderedDocument
from careeros_resume.domain.draft import DatedEntry, Education, Experience, ResumeDraft, Skill

PDF_LOCK = Lock()
PERIOD_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
Block = tuple[int, str]


def _dates(entry: DatedEntry) -> str:
    if entry.current:
        return f"{entry.start_date} – Present"
    if entry.end_date is not None:
        return f"{entry.start_date} – {entry.end_date}"
    return str(entry.start_date)


def _blocks(draft: ResumeDraft) -> list[Block]:
    blocks = [(0, "Resume")]
    for heading, entry_type in (
        ("Experience", Experience),
        ("Education", Education),
        ("Skills", Skill),
    ):
        entries = [entry for entry in draft.entries if isinstance(entry, entry_type)]
        if not entries:
            continue
        blocks.append((1, heading))
        for entry in entries:
            if isinstance(entry, Experience):
                blocks.append((2, f"{entry.title} | {entry.employer}"))
                blocks.append((2, _dates(entry)))
                if entry.description:
                    blocks.append((2, entry.description))
            elif isinstance(entry, Education):
                blocks.append((2, f"{entry.qualification} | {entry.institution}"))
                blocks.append((2, _dates(entry)))
                if entry.field_of_study:
                    blocks.append((2, entry.field_of_study))
            else:
                level = f" ({entry.level}, self-declared)" if entry.level is not None else ""
                blocks.append((2, entry.name + level))
    return blocks


def _compact(text: str) -> str:
    return "".join(text.split())


class DocumentRenderer:
    def __init__(self, font: Path | None = None) -> None:
        resource = font if font is not None else files("reportlab").joinpath("fonts", "Vera.ttf")
        with as_file(resource) as path, PDF_LOCK:
            self._font = "Resume" + sha256(path.read_bytes()).hexdigest()[:16]
            embedded_font = TTFont(self._font, str(path))
            self._glyphs = frozenset(
                code for code, glyph in embedded_font.face.charToGlyph.items() if glyph
            )
            if self._font not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(embedded_font)

    def render(self, draft: ResumeDraft) -> tuple[RenderedDocument, ...]:
        blocks = _blocks(draft)
        if any(
            ord(character) not in self._glyphs and not character.isspace()
            for _, text in blocks
            for character in text
        ):
            raise DraftInputInvalid
        try:
            pdf = self._pdf(blocks)
            docx = self._docx(blocks)
        except (LayoutError, ValueError):
            raise DraftInputInvalid from None
        expected = _compact(" ".join(text for _, text in blocks))
        pdf_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        docx_text = "\n".join(item.text for item in Document(BytesIO(docx)).paragraphs)
        if _compact(pdf_text) != expected or _compact(docx_text) != expected:
            raise DraftInputInvalid
        return (
            RenderedDocument(format="pdf", data=pdf),
            RenderedDocument(format="docx", data=docx),
        )

    def _pdf(self, blocks: list[Block]) -> bytes:
        target = BytesIO()
        styles = [
            ParagraphStyle(
                f"Resume{index}",
                fontName=self._font,
                fontSize=size,
                leading=size + 4,
                spaceBefore=space,
                spaceAfter=4,
                keepWithNext=int(index < 2),
            )
            for index, (size, space) in enumerate(((22, 0), (14, 10), (10, 0)))
        ]
        paragraphs: list[Flowable] = [
            Paragraph("<br/>".join(escape(line) for line in text.splitlines()), styles[level])
            for level, text in blocks
        ]
        with PDF_LOCK:
            SimpleDocTemplate(
                target,
                pagesize=A4,
                title="Resume",
                author="",
                creator="CareerOS",
                invariant=True,
                leftMargin=54,
                rightMargin=54,
                topMargin=48,
                bottomMargin=48,
            ).build(paragraphs)
        return target.getvalue()

    @staticmethod
    def _docx(blocks: list[Block]) -> bytes:
        document = Document()
        properties = document.core_properties
        properties.title = "Resume"
        properties.author = properties.last_modified_by = properties.comments = ""
        properties.created = properties.modified = PERIOD_EPOCH
        for level, text in blocks:
            if level < 2:
                document.add_heading(text, level=level)
            else:
                document.add_paragraph(text)
        raw = BytesIO()
        document.save(raw)
        target = BytesIO()
        with ZipFile(raw) as archive, ZipFile(target, "w", compression=ZIP_DEFLATED) as output:
            for name in sorted(archive.namelist()):
                output.writestr(ZipInfo(name), archive.read(name), compress_type=ZIP_DEFLATED)
        return target.getvalue()
