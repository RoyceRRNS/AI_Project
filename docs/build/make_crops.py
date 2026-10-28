"""Column-width report figures cut from the full screenshots (2880 x 1800, 2x scale).

    .venv/Scripts/python docs/build/make_crops.py
"""

from pathlib import Path

from PIL import Image

FIG = Path(__file__).resolve().parents[1] / "figures"


def stack(parts: list[Image.Image], width: int, gap: int = 24) -> Image.Image:
    scaled = [p.resize((width, round(p.height * width / p.width)), Image.LANCZOS) for p in parts]
    out = Image.new("RGB", (width, sum(p.height for p in scaled) + gap * (len(scaled) - 1)), "white")
    y = 0
    for p in scaled:
        out.paste(p, (0, y))
        y += p.height + gap
    return out


chat = Image.open(FIG / "ui_chat_viewer.png").convert("RGB")
answer = chat.crop((30, 240, 1545, 1360))        # question, cited answer, badge, source
viewer = chat.crop((1590, 1235, 2860, 1655))     # highlighted paragraph in the PDF viewer
stack([answer, viewer], 1515).save(FIG / "fig_chat.png", optimize=True)

quiz = Image.open(FIG / "ui_quiz_feedback.png").convert("RGB")
quiz.crop((255, 215, 1945, 1800)).save(FIG / "fig_quiz.png", optimize=True)
print("saved fig_chat.png, fig_quiz.png")
