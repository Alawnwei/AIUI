import xToolkit,pytest,allure # 第三方模块(别人已经写好的代码
from AIDemo import process_by_ai

all_case = xToolkit.xfile.read("WEB测试用例.xls").excel_to_dict(sheet=1)
print("所有测试用例:", all_case)


@pytest.mark.parametrize('case', all_case)  # 参数化机制 解析用例
@pytest.mark.asyncio
async def test_case_exec(case):
    allure.dynamic.title(case["用例标题"])
    test_result = await process_by_ai(case["用例描述"])
    assert test_result == "测试通过", "测试不通过"


