"""`tools/cardgen_grid.py` — 조건 폴더 × 카드 이름 격자 (#557 E1)."""

from pathlib import Path

from PIL import Image

from tools.cardgen_grid import HEADER_H, LABEL_W, build_grid, main


def _card(path: Path, color) -> None:
    Image.new("RGB", (994, 1582), color).save(path)


def test_grid_layout_and_missing_cell(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _card(a / "p_4_s1.png", (255, 0, 0))
    _card(a / "p_9_s1.png", (255, 0, 0))
    _card(b / "p_4_s1.png", (0, 0, 255))   # b 에는 9월이 없다

    grid = build_grid([("A", a), ("B", b)], ["p_4_s1", "p_9_s1"], thumb_width=100)

    thumb_h = round(1582 * 100 / 994)
    assert grid.size == (LABEL_W + 2 * 100, HEADER_H + 2 * thumb_h)
    assert grid.getpixel((LABEL_W + 50, HEADER_H + thumb_h // 2)) == (255, 0, 0)
    assert grid.getpixel((LABEL_W + 150, HEADER_H + thumb_h // 2)) == (0, 0, 255)
    assert grid.getpixel((LABEL_W + 150, HEADER_H + thumb_h + thumb_h // 2)) == (128, 128, 128)


def test_main_writes_png(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    _card(a / "p_4_s1.png", (0, 255, 0))
    out = tmp_path / "grid.png"

    assert main(["--col", f"A={a}", "--names", "p_4_s1", "--out", str(out), "--thumb-width", "50"]) == 0
    assert Image.open(out).size[0] == LABEL_W + 50
