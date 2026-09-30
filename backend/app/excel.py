from pathlib import Path

from openpyxl import Workbook

from .models import ParseResult
from .storage import build_output_filename, ensure_output_dir


def build_excel_rows(result: ParseResult) -> list[list]:
    rows = []
    for idx, inst in enumerate(result.instructions, start=1):
        rows.append(
            [
                "",
                inst.start_time,
                inst.end_time,
                result.client_account or "",
                "",
                "",
                inst.symbol,
                inst.exchange or "",
                inst.hedge_flag or "",
                inst.side,
                inst.offset,
                inst.lots,
                inst.lots,
                "",
            ]
        )
    return rows


def export_result_to_xlsx(result: ParseResult, output_dir: str) -> str:
    ensure_output_dir(output_dir)
    wb = Workbook()
    ws = wb.active
    ws.title = "instructions"
    ws.append(
    [
        "名称", #空
        "开始时间",
        "结束时间",
        "客户账号",
        "客户名称",#空
        "优先腿", #空
        "合约",
        "交易所",
        "投机套保",
        "买卖方向",
        "开平方向",
        "数量（手）",
        "切片次数",
        "备注", #空
    ]
)

    for row in build_excel_rows(result):
        ws.append(row)

    filename_prefix = result.client_account or "instruction"
    file_path = Path(output_dir) / build_output_filename(prefix=filename_prefix)
    wb.save(file_path)
    return str(file_path)
