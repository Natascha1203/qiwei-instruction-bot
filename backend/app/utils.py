from datetime import datetime


def now_str(fmt: str = "%Y%m%d%H%M%S") -> str:
    return datetime.now().strftime(fmt)


def normalize_cn_punctuation(text: str) -> str:
    return (
        text.replace("，", ",")
        .replace("：", ":")
        .replace("；", ";")
        .replace("（", "(")
        .replace("）", ")")
        .replace("。", ".")
    )


def safe_strip(text: str) -> str:
    return text.strip() if text else ""


def to_hhmmss(value: str) -> str:
    hh, mm, ss = value.split(":")
    return f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}"
