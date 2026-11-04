"""Page order in the Markdown: top to bottom, left to right. Invented data only."""

from types import SimpleNamespace

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

_WIDTH, _HEIGHT = A4
_LOREM = (
    "Ez egy kitalalt szoveg a teszthez, tobb soron at tart, hogy a felismero "
    "bekezdesnek lassa. Nincs benne valodi adat, csak mintaszavak vannak benne. "
)


def _box(text, page=1, *, top, height=10.0, left=60.0):
    return SimpleNamespace(
        text=text, page_no=page, t=top, b=top - height, l=left, r=left + 100.0, cid=0
    )


def _sorted_texts(*boxes):
    from medical_redactor_onnx.register_docling import _sort_top_to_bottom

    for cid, box in enumerate(boxes):
        box.cid = cid
    return [box.text for box in _sort_top_to_bottom(list(boxes))]


def _draw(pdf_canvas, text, *, left=60, down, width=460, size=10, font="Helvetica"):
    """Draw text with its top `down` points below the top of the page."""
    style = ParagraphStyle("body", fontName=font, fontSize=size, leading=size * 1.25)
    paragraph = Paragraph(text, style)
    _, height = paragraph.wrap(width, 400)
    paragraph.drawOn(pdf_canvas, left, _HEIGHT - down - height)


def _assert_in_order(text, *parts):
    positions = [text.index(part) for part in parts]
    assert positions == sorted(positions), f"wrong order in:\n{text}"


# --- the sort itself ---


def test_higher_on_the_page_comes_first():
    # Docling gives bottom-left coordinates: a larger top value is higher up.
    assert _sorted_texts(
        _box("bottom", top=100), _box("top", top=700), _box("middle", top=400)
    ) == ["top", "middle", "bottom"]


def test_pages_stay_in_page_order():
    assert _sorted_texts(
        _box("page 2 top", page=2, top=700),
        _box("page 1 bottom", page=1, top=100),
        _box("page 1 top", page=1, top=700),
    ) == ["page 1 top", "page 1 bottom", "page 2 top"]


def test_side_by_side_boxes_read_left_to_right_when_tops_differ_slightly():
    # A value box a little higher than its label must still follow the label.
    assert _sorted_texts(
        _box("value", top=702, left=400), _box("label", top=700, left=60)
    ) == ["label", "value"]


def test_same_top_on_two_pages_is_not_one_row():
    assert _sorted_texts(
        _box("page 2 left", page=2, top=700, left=60),
        _box("page 1 right", page=1, top=700, left=400),
    ) == ["page 1 right", "page 2 left"]


def test_tall_box_does_not_pull_a_column_of_short_boxes_out_of_order():
    # Short boxes beside a tall one overlap it fully. They are not one row with
    # it: their order inside the column must still come from their height.
    assert _sorted_texts(
        _box("tall sidebar", top=700, height=300, left=60),
        _box("column first", top=695, left=300.5),
        _box("column second", top=600, left=300.2),
        _box("column third", top=500, left=300),
    ) == ["tall sidebar", "column first", "column second", "column third"]


# --- through the real converter ---


def test_converter_reads_each_page_top_to_bottom(tmp_path):
    from redactor import build_docling_converter, convert_pdf

    pdf = tmp_path / "minta.pdf"
    pdf_canvas = canvas.Canvas(str(pdf), pagesize=A4)
    _draw(pdf_canvas, "Elso oldal, felso bekezdes.", down=80)
    _draw(pdf_canvas, "Elso oldal, kozepso bekezdes.", down=300)
    _draw(pdf_canvas, "Elso oldal, also bekezdes.", down=520)
    pdf_canvas.showPage()
    _draw(pdf_canvas, "Masodik oldal, felso bekezdes.", down=80)
    _draw(pdf_canvas, "Masodik oldal, also bekezdes.", down=520)
    pdf_canvas.showPage()
    pdf_canvas.save()

    markdown = convert_pdf(str(pdf), build_docling_converter())

    _assert_in_order(
        markdown,
        "Elso oldal, felso",
        "Elso oldal, kozepso",
        "Elso oldal, also",
        "Masodik oldal, felso",
        "Masodik oldal, also",
    )


def test_converter_reads_side_by_side_paragraphs_left_to_right(tmp_path):
    from redactor import build_docling_converter, convert_pdf

    pdf = tmp_path / "hasabok.pdf"
    pdf_canvas = canvas.Canvas(str(pdf), pagesize=A4)
    # The right-hand paragraph of each pair starts 3 points higher.
    _draw(pdf_canvas, "BAL ELSO. " + _LOREM * 2, left=60, down=100, width=220)
    _draw(pdf_canvas, "JOBB ELSO. " + _LOREM * 2, left=320, down=97, width=220)
    _draw(pdf_canvas, "BAL MASODIK. " + _LOREM * 2, left=60, down=300, width=220)
    _draw(pdf_canvas, "JOBB MASODIK. " + _LOREM * 2, left=320, down=297, width=220)
    pdf_canvas.showPage()
    pdf_canvas.save()

    markdown = convert_pdf(str(pdf), build_docling_converter())

    _assert_in_order(markdown, "BAL ELSO", "JOBB ELSO", "BAL MASODIK", "JOBB MASODIK")


def test_rules_see_a_label_before_the_value_below_it(tmp_path):
    # The TAJ and birth date rules read the text before a value, and the name
    # rule reads the text after a label. With a label on the line above, this
    # only works when the page reads top to bottom.
    from conftest import EmptyNerPipeline
    from redactor import build_docling_converter, process_file

    pdf = tmp_path / "cimkek.pdf"
    pdf_canvas = canvas.Canvas(str(pdf), pagesize=A4)
    rows = (
        ("Beteg neve:", "Teszt Elek"),
        # Fails the TAJ checksum, so only the label marks it as a TAJ number.
        ("TAJ szam:", "123 456 789"),
        ("Szuletesi datum:", "1980. 01. 02."),
    )
    for index, (label, value) in enumerate(rows):
        # The bold label keeps Docling from merging label and value into one box.
        _draw(pdf_canvas, label, down=80 + index * 160, font="Helvetica-Bold")
        _draw(pdf_canvas, value, down=98 + index * 160, size=11)
    pdf_canvas.showPage()
    pdf_canvas.save()

    redacted = process_file(str(pdf), EmptyNerPipeline(), build_docling_converter())

    for _label, value in rows:
        assert value not in redacted, redacted


def test_rules_see_a_label_below_its_value(tmp_path):
    # Some forms print the caption under the filled-in value. The value then
    # comes first in the text, so the rules must also read the line below it.
    from conftest import EmptyNerPipeline
    from redactor import build_docling_converter, process_file

    pdf = tmp_path / "alairas.pdf"
    pdf_canvas = canvas.Canvas(str(pdf), pagesize=A4)
    rows = (
        ("Teszt Elek", "Beteg neve"),
        # Fails the TAJ checksum, so only the label marks it as a TAJ number.
        ("123 456 789", "TAJ szam"),
        ("1980. 01. 02.", "Szuletesi datum"),
    )
    for index, (value, label) in enumerate(rows):
        _draw(pdf_canvas, value, down=80 + index * 160, size=11)
        _draw(pdf_canvas, label, down=98 + index * 160, font="Helvetica-Bold")
    pdf_canvas.showPage()
    pdf_canvas.save()

    redacted = process_file(str(pdf), EmptyNerPipeline(), build_docling_converter())

    for value, _label in rows:
        assert value not in redacted, redacted
