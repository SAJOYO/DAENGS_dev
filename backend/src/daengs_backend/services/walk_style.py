"""산책 표시 정책 v1. 개인의 선택은 앱 기기에 저장하며 원본 기록에는 넣지 않는다."""

from daengs_backend.schemas.walk_style import WalkColorTheme, WalkStylePolicy


def walk_style_policy() -> WalkStylePolicy:
    themes = [
        ("pink", "핑크", (244, 72, 153)),
        ("red", "빨강", (224, 36, 36)),
        ("blue", "파랑", (48, 132, 244)),
        ("purple", "보라", (161, 92, 235)),
    ]
    return WalkStylePolicy(
        themes=[
            WalkColorTheme(
                id=key,
                label=label,
                colors=[
                    "#"
                    + "".join(f"{int(24 + (channel - 24) * i / 4 + 0.5):02x}" for channel in end)
                    for i in range(5)
                ],
            )
            for key, label, end in themes
        ]
    )
