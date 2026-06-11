import pytest, os  # 第三方模块(别人已经写好的代码)

if __name__ == '__main__':
    pytest.main(['-s', '-v','--alluredir=allure-results'])  # allure 特定的 toNo
    os.system(r"allure generate -c -o 测试报告") #安装并配置 allure 环境变量
