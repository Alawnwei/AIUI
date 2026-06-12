import pytest, allure
import pandas as pd

from AIDemo import process_by_ai

all_case = pd.read_excel("WEB测试用例.xlsx", sheet_name=0, engine='openpyxl').to_dict(orient='records')
print("所有测试用例:", all_case)


@pytest.mark.parametrize('case', all_case)  # 参数化机制 解析用例
@pytest.mark.asyncio
async def test_case_exec(case):
    allure.dynamic.title(case["用例标题"])
    test_result = await process_by_ai(case)
    assert test_result == "测试通过", "测试不通过"

