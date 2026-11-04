import logging

import pytest

import redactor
from redactor import FileEvent, process_pdfs


MARKDOWN = """
Beteg neve: Teszt Elek
TAJ szam: 123 456 789
"""


def _run(tmp_path, monkeypatch, converter):
    from conftest import EmptyNerPipeline

    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out_dir = tmp_path / "out"

    monkeypatch.setattr(redactor, "build_docling_converter", lambda: converter)
    monkeypatch.setattr(redactor, "load_ner_model", lambda: EmptyNerPipeline())

    events: list[FileEvent] = []
    process_pdfs([str(pdf)], str(out_dir), on_file_event=events.append)
    return pdf, events


def test_process_pdfs_emits_stage_events_with_counts(tmp_path, monkeypatch):
    from conftest import FakeConverter

    pdf, events = _run(tmp_path, monkeypatch, FakeConverter(MARKDOWN))

    assert [e.stage for e in events] == ["converting", "redacting", "done"]
    assert all(e.path == str(pdf) for e in events)

    done = events[-1]
    assert done.counts["NAME"] >= 1
    assert done.counts["TAJ"] == 1
    assert done.output_name and done.output_name.endswith(".md")
    assert done.error is None


def test_process_pdfs_emits_failed_event_on_error(tmp_path, monkeypatch):
    class BrokenConverter:
        def convert(self, path):
            raise RuntimeError("boom")

    pdf, events = _run(tmp_path, monkeypatch, BrokenConverter())

    assert [e.stage for e in events] == ["converting", "failed"]
    assert "boom" in events[-1].error


def test_process_pdfs_records_failure_traceback(tmp_path, monkeypatch, caplog):
    class BrokenConverter:
        def convert(self, path):
            raise RuntimeError("backend detail")

    with caplog.at_level(logging.ERROR):
        _run(tmp_path, monkeypatch, BrokenConverter())

    assert "PDF processing failed" in caplog.text
    assert "backend detail" in caplog.text


def test_process_file_return_type_unchanged(tmp_path):
    from conftest import EmptyNerPipeline, FakeConverter

    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    result = redactor.process_file(str(pdf), EmptyNerPipeline(), FakeConverter(MARKDOWN))
    assert isinstance(result, str)


def test_windows_conversion_uses_stream_for_unicode_pdf_path(tmp_path, monkeypatch):
    pdf = tmp_path / "Kovács-Szűcs Ádám lelet.pdf"
    pdf_bytes = b"%PDF-1.4\nunicode-path-regression"
    pdf.write_bytes(pdf_bytes)

    class CapturingConverter:
        source = None

        def convert(self, source):
            self.source = source
            return FakeConversion(MARKDOWN)

    from conftest import FakeConversion

    converter = CapturingConverter()
    monkeypatch.setattr(redactor, "_is_windows", lambda: True)

    redactor.convert_pdf(str(pdf), converter)

    assert converter.source.name == pdf.name
    assert converter.source.stream.read() == pdf_bytes


class PartialConverter:
    """Docling's answer when a stage failed on some pages but not on all."""

    def __init__(self, errors=("Page 2: invented failure", "Page 3: invented failure")):
        self._errors = errors

    def convert(self, _source):
        from types import SimpleNamespace

        from conftest import FakeConversion
        from docling.datamodel.base_models import ConversionStatus

        return FakeConversion(
            MARKDOWN,
            ConversionStatus.PARTIAL_SUCCESS,
            [SimpleNamespace(error_message=message) for message in self._errors],
        )


def test_convert_pdf_detailed_reports_what_was_lost(tmp_path):
    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    markdown, problems = redactor.convert_pdf_detailed(str(pdf), PartialConverter())

    assert "Teszt Elek" in markdown
    assert problems == ["Page 2: invented failure", "Page 3: invented failure"]


def test_convert_pdf_detailed_finds_no_problem_in_a_complete_result(tmp_path):
    from conftest import FakeConverter

    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    _markdown, problems = redactor.convert_pdf_detailed(str(pdf), FakeConverter(MARKDOWN))

    assert problems == []


def test_partial_result_without_error_details_is_still_reported(tmp_path):
    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    _markdown, problems = redactor.convert_pdf_detailed(str(pdf), PartialConverter(errors=()))

    assert problems == ["Docling status: partial_success"]


def test_callers_without_a_report_channel_get_an_error_not_a_silent_loss(tmp_path):
    from conftest import EmptyNerPipeline

    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    with pytest.raises(redactor.PartialConversionError, match="Page 2") as caught:
        redactor.convert_pdf(str(pdf), PartialConverter())
    assert caught.value.problems[0].startswith("Page 2")

    with pytest.raises(redactor.PartialConversionError, match="Page 2"):
        redactor.process_file(str(pdf), EmptyNerPipeline(), PartialConverter())


def test_process_pdfs_saves_and_flags_a_partial_conversion(tmp_path, monkeypatch):
    pdf, events = _run(tmp_path, monkeypatch, PartialConverter())

    assert [e.stage for e in events] == ["converting", "redacting", "partial"]
    partial = events[-1]
    assert partial.path == str(pdf)
    assert partial.counts["TAJ"] == 1
    assert "Page 2: invented failure" in partial.error
    assert "Page 3: invented failure" in partial.error

    # What was converted still gets redacted and saved.
    saved = (tmp_path / "out" / partial.output_name).read_text(encoding="utf-8")
    assert "[REDACTED_" in saved
    assert "Teszt Elek" not in saved


def test_complete_conversion_has_no_error_text(tmp_path, monkeypatch):
    from conftest import FakeConverter

    _pdf, events = _run(tmp_path, monkeypatch, FakeConverter(MARKDOWN))

    assert events[-1].stage == "done"
    assert events[-1].error is None


def test_log_warns_about_a_partial_conversion_and_counts_it(tmp_path, monkeypatch):
    from conftest import EmptyNerPipeline

    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(redactor, "build_docling_converter", PartialConverter)
    monkeypatch.setattr(redactor, "load_ner_model", EmptyNerPipeline)

    lines: list[str] = []
    process_pdfs([str(pdf)], str(tmp_path / "out"), log=lines.append)

    assert any("WARNING" in line and "Page 2" in line for line in lines), lines
    assert any("0/1" in line and "1 incomplete" in line for line in lines), lines


def test_partial_conversion_reaches_the_queue_row(qapp, tmp_path, monkeypatch):
    from conftest import EmptyNerPipeline
    from gui import i18n
    from gui.queue_model import QueueModel
    from gui.worker import RedactionWorker
    from PySide6.QtCore import Qt

    i18n.set_language("en")
    pdf = tmp_path / "lelet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(redactor, "build_docling_converter", PartialConverter)
    monkeypatch.setattr(redactor, "load_ner_model", EmptyNerPipeline)
    queue = QueueModel()
    queue.add_paths([str(pdf)])

    worker = RedactionWorker([str(pdf)], str(tmp_path / "out"))
    worker.file_event.connect(queue.apply_event)
    worker.run()  # on this thread, so the row updates before the checks

    assert queue.data(queue.index(0, 1), Qt.DisplayRole) == "Incomplete"
    assert "Page 2: invented failure" in queue.data(queue.index(0, 1), Qt.ToolTipRole)
