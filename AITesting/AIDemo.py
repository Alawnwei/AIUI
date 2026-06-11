import asyncio
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from browser_use import Browser, BrowserConfig, Agent  # 引入三方模块

load_dotenv()


llm = ChatOpenAI(
    api_key="sk-d88a8e8f14894be28707adbae1b024c9",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    model="qwen-plus"
)

# 浏览器配置，用什么浏览器进行测试---浏览器配置：使用 Playwright 内置 Chromium，非无头模式
config = BrowserConfig(
    headless=False, # 展示界面
    disable_security=True, # 禁用部分安全限制，便于自动化
    # chrome_instance_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", # edge路径
# 不指定 chrome_instance_path，让 Playwright 自动管理浏览器生命周期
)
browser = Browser(config=config)

# 测试过程
async def process_by_ai(): # 定义了一个方法，是调用browser-use框架
    agent = Agent(
        llm=llm,
        browser=browser,
        message_context="你正在进行web软件自动化测试，你最终返回的结果是测试通过或者不通过，不需要返回其他内容",
        task="""1. 打开网页 http://novel.hctestedu.com/
                2. 搜索框中输入"反派"，点击搜索
                3. 返回搜索结果列表序号1对应的名称
                结果：如果这个名称等于 斗破苍穹 返回"测试通过"，不等于则返回"测试不通过"
            """
    )
    history = await agent.run()
    result = history.final_result()
    print("执行结果:", result)
    # 显式关闭浏览器，释放资源
    await browser.close()
    # 短暂等待，让子进程有时间彻底退出
    await asyncio.sleep(0.2)

if __name__ == "__main__":
    asyncio.run(process_by_ai())