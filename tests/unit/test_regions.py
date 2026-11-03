import pymupdf
import pytest

from course_notes_rag.ingestion.regions import MAX_REGION_PIXELS, RegionError, render_region


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "notes.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.draw_rect(pymupdf.Rect(50, 100, 150, 130), color=(0, 0, 0), fill=(0, 0, 0))
        rotated = doc.new_page(width=300, height=400)
        rotated.draw_rect(pymupdf.Rect(20, 30, 80, 60), color=(0, 0, 0), fill=(0, 0, 0))
        rotated.set_rotation(90)
        doc.save(path)
    return path


def pixmap(png: bytes) -> pymupdf.Pixmap:
    return pymupdf.Pixmap(png)


def dark_share(pix: pymupdf.Pixmap) -> float:
    dark = sum(1 for y in range(pix.height) for x in range(pix.width) if pix.pixel(x, y)[0] < 60)
    return dark / (pix.width * pix.height)


def test_renders_the_area_with_a_small_margin(pdf):
    png = render_region(pdf, 1, 50, 100, 150, 130)
    assert png.startswith(b"\x89PNG")
    pix = pixmap(png)
    scale = 200 / 72
    assert pix.width == pytest.approx((100 + 8) * scale, abs=2)
    assert pix.height == pytest.approx((30 + 8) * scale, abs=2)
    assert 0.6 < dark_share(pix) < 0.9  # the box, with a white margin around it


def test_areas_follow_the_page_as_displayed_when_it_is_rotated(pdf):
    # The box drawn at (20, 30)-(80, 60) appears at (340, 20)-(370, 80) once the page is turned.
    pix = pixmap(render_region(pdf, 2, 340, 20, 370, 80))
    assert dark_share(pix) > 0.6


def test_an_area_past_the_edge_is_clipped_to_the_page(pdf):
    pix = pixmap(render_region(pdf, 1, 250, 350, 900, 900))
    assert pix.width == pytest.approx((300 - 246) * 200 / 72, abs=2)


def test_a_large_area_is_drawn_at_a_lower_resolution(pdf):
    pix = pixmap(render_region(pdf, 1, 0, 0, 300, 400))
    assert pix.width * pix.height <= MAX_REGION_PIXELS * 1.01


@pytest.mark.parametrize("page, box, message", [
    (0, (50, 100, 150, 130), "Page 0"),
    (3, (50, 100, 150, 130), "Page 3"),
    (1, (150, 100, 50, 130), "positive width"),
    (1, (50, 130, 150, 130), "positive width"),
    (1, (50, float("nan"), 150, 130), "positive width"),
    (1, (500, 600, 700, 800), "outside the page"),
])
def test_bad_areas_are_refused(pdf, page, box, message):
    with pytest.raises(RegionError, match=message):
        render_region(pdf, page, *box)


def test_a_file_that_is_not_a_pdf_is_refused(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(RegionError, match="not a readable PDF"):
        render_region(bad, 1, 0, 0, 10, 10)
